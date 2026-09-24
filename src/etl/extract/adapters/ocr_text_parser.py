"""Heuristic mapping of raw OCR text into Candidate* shapes.

Engine-agnostic on purpose: this module takes a flat block of already-
recognized text and never calls an OCR engine itself. RapidOCR (the
current caller, in rapidocr_adapter.py) has no notion of receipt structure
either — it returns individual detected text boxes, joined into the same
flat text shape this module expects. Everything here is regex/line-position
heuristics tuned against varied synthetic fixture receipts, not a real
parsing grammar. Fields this can't find stay None/omitted; the calling
adapter derives confidence from what actually came out, per DESIGN-V3.md's
third confidence origin (arithmetic validation), since neither RapidOCR nor
Tesseract-family engines give native per-field confidence for structured
fields like "is this the date field."
"""

from __future__ import annotations

import re
from datetime import date, datetime

_MONEY = r"[$₦£€]?\s?(\d[\d,]*\.\d{2})"
_TOTAL_LINE = re.compile(r"\btotal\b(?!.*subtotal)", re.IGNORECASE)
_SUBTOTAL_LINE = re.compile(r"\bsub\s?-?total\b", re.IGNORECASE)
_TAX_LINE = re.compile(r"\b(vat|tax)\b", re.IGNORECASE)
_DISCOUNT_LINE = re.compile(r"\bdiscount\b", re.IGNORECASE)
_PHONE = re.compile(r"(\+?\d[\d\-\s()]{7,}\d)")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_WEBSITE = re.compile(r"\b(?:www\.|https?://)\S+\b", re.IGNORECASE)
_DATE_FORMATS = (
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%d-%m-%Y",
    "%d %b %Y",
    "%b %d, %Y",
    "%B %d, %Y",
    "%d %B %Y",
    "%d-%b-%y",
)
_DATE_CANDIDATE = re.compile(
    r"\b(\d{4}-\d{2}-\d{2}|\d{1,2}-[A-Za-z]{3}-\d{2}\b|\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|"
    r"\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}|[A-Za-z]{3,9}\s+\d{1,2}[,.]?\s+\d{4})\b"
)
_TXN_REF = re.compile(
    r"\b(?:inv(?:oice)?|ref|receipt|txn|transaction)\s*[#:no.]*\s*([A-Za-z0-9-]{3,})",
    re.IGNORECASE,
)
_ITEM_LINE = re.compile(
    rf"^(?P<description>.+?)\s+(?P<quantity>\d+(?:\.\d+)?)\s*[xX@]\s*"
    rf"{_MONEY}\s*=?\s*{_MONEY}?$"
)
_ITEM_LINE_NO_QTY = re.compile(rf"^(?P<description>.+?)\s+{_MONEY}$")


class ParsedReceipt:
    def __init__(self) -> None:
        self.store_name: str | None = None
        self.store_address: str | None = None
        self.phone: str | None = None
        self.email: str | None = None
        self.website: str | None = None
        self.transaction_ref: str | None = None
        self.date: date | None = None
        self.subtotal: float | None = None
        self.discount: float | None = None
        self.tax: float | None = None
        self.total: float | None = None
        self.line_items: list[ParsedLineItem] = []


class ParsedLineItem:
    def __init__(
        self, description: str, quantity: float, unit_price: float, line_total: float
    ) -> None:
        self.description = description
        self.quantity = quantity
        self.unit_price = unit_price
        self.line_total = line_total


def parse(raw_text: str) -> ParsedReceipt:
    """Single pass over the OCR lines: each line is classified once (as a
    contact/date/ref line, a total/subtotal/tax/discount line, or a
    candidate line item), then routed accordingly. Avoids re-running the
    same regex checks in separate passes over the same lines."""
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    result = ParsedReceipt()

    header_candidates: list[str] = []
    for line in lines:
        is_header_field = _apply_header_line(line, result)
        _apply_totals_line(line, result)
        item = _parse_item_line(line)
        if item is not None:
            result.line_items.append(item)

        if not is_header_field and len(header_candidates) < 2:
            header_candidates.append(line)

    if header_candidates:
        result.store_name = header_candidates[0]
    if len(header_candidates) > 1:
        result.store_address = header_candidates[1]

    if result.total is None and result.subtotal is not None:
        # Some real receipts print only a "SUB TOTAL" line with no separate
        # "TOTAL" line at all (no tax/discount shown as its own line) —
        # confirmed against a real receipt. In that case the subtotal is
        # the actual grand total, not a partial figure awaiting adjustment.
        result.total = result.subtotal

    return result


def _apply_header_line(line: str, result: ParsedReceipt) -> bool:
    """Extracts any contact/date/ref field present on this line. Returns
    True if the line matched one of those fields (so it's excluded from
    store name/address candidacy)."""
    matched = False

    if (match := _EMAIL.search(line)) and result.email is None:
        result.email = match.group(0)
        matched = True
    if (match := _WEBSITE.search(line)) and result.website is None:
        result.website = match.group(0)
        matched = True
    if (match := _PHONE.search(line)) and result.phone is None:
        result.phone = match.group(1).strip()
        matched = True
    if (match := _TXN_REF.search(line)) and result.transaction_ref is None:
        result.transaction_ref = match.group(1)
        matched = True
    if match := _DATE_CANDIDATE.search(line):
        matched = True
        if result.date is None:
            result.date = _parse_date(match.group(1))

    return matched


def _parse_date(raw: str) -> date | None:
    # Strips both comma and period: OCR commonly misreads "Month D, Year"'s
    # comma as a period (confirmed against a real receipt), and neither
    # character carries meaning once the date format itself is known.
    normalized = raw.replace(",", "").replace(".", "")
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(normalized, fmt.replace(",", "")).date()  # noqa: DTZ007
        except ValueError:
            continue
    return None


def _apply_totals_line(line: str, result: ParsedReceipt) -> None:
    money_matches = re.findall(_MONEY, line)
    if not money_matches:
        return
    # Some real receipts print an item count before the money amount on the
    # same line (e.g. "SUB TOTAL 6.00 8,800.00" — 6 items, then the actual
    # total) — confirmed against a real receipt. The amount is always the
    # rightmost money-shaped value on a totals line, never an earlier one.
    value = float(money_matches[-1].replace(",", ""))

    if _SUBTOTAL_LINE.search(line):
        result.subtotal = value
    elif _DISCOUNT_LINE.search(line):
        result.discount = value
    elif _TAX_LINE.search(line):
        result.tax = value
    elif _TOTAL_LINE.search(line):
        result.total = value


_TOTALS_LINE_PATTERNS = (_TOTAL_LINE, _SUBTOTAL_LINE, _TAX_LINE, _DISCOUNT_LINE)


def _parse_item_line(line: str) -> ParsedLineItem | None:
    if any(pattern.search(line) for pattern in _TOTALS_LINE_PATTERNS):
        return None

    if match := _ITEM_LINE.match(line):
        quantity = float(match.group("quantity"))
        unit_price = float(match.group(3).replace(",", ""))
        line_total_raw = match.group(4)
        line_total = (
            float(line_total_raw.replace(",", ""))
            if line_total_raw
            else round(quantity * unit_price, 2)
        )
        return ParsedLineItem(
            description=match.group("description").strip(),
            quantity=quantity,
            unit_price=unit_price,
            line_total=line_total,
        )

    if match := _ITEM_LINE_NO_QTY.match(line):
        price = float(match.group(2).replace(",", ""))
        return ParsedLineItem(
            description=match.group("description").strip(),
            quantity=1.0,
            unit_price=price,
            line_total=price,
        )

    return None
