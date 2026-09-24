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

_CURRENCY = r"[$₦£€N≈]?"
"""Currency prefixes seen in real OCR output. `N` and `≈` are both common
OCR misreads of `₦` (Naira) — confirmed on real receipts, sometimes both on
the same receipt, where one item's `₦` reads as `N` and another's as `≈`."""

_MONEY = rf"{_CURRENCY}\s?(\d[\d,]*\.\d{{2}})"
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

# Some POS receipts print one item across two physical lines: a numbered
# description line ("#1:EVERYMAN MULTIVITAMIN"), then a quantity/price line
# ("(3) Unit × N2,750.00 N8,250.00"). Confirmed on real receipts from one
# pharmacy chain. Neither single-line item pattern above can match this
# shape, so the parser checks consecutive line pairs for it.
_ITEM_NUMBERED_DESCRIPTION = re.compile(r"^#\s?\d+\s*[:.]?\s*(?P<description>.+)$")
_ITEM_QTY_PRICE_LINE = re.compile(
    rf"^\(?(?P<quantity>\d+(?:\.\d+)?)\)?\s*(?:unit|pcs?|ea)?\s*[x×*]\s*"
    rf"{_MONEY}\s*{_MONEY}?$",
    re.IGNORECASE,
)


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
    index = 0
    while index < len(lines):
        line = lines[index]

        # Two-line items are checked before anything else: the description
        # half ("#1:EVERYMAN MULTIVITAMIN") would otherwise be taken as a
        # header candidate, and the quantity/price half would be misread by
        # _ITEM_LINE_NO_QTY as its own separate single-line item.
        next_line = lines[index + 1] if index + 1 < len(lines) else None
        paired = _parse_two_line_item(line, next_line)
        if paired is not None:
            result.line_items.append(paired)
            index += 2
            continue

        is_header_field = _apply_header_line(line, result)
        _apply_totals_line(line, result)
        item = _parse_item_line(line)
        if item is not None:
            result.line_items.append(item)

        if not is_header_field and len(header_candidates) < _HEADER_LOOKAHEAD:
            header_candidates.append(line)
        index += 1

    store_name_index = _pick_store_name_index(header_candidates)
    if store_name_index is not None:
        result.store_name = header_candidates[store_name_index]
        remaining = header_candidates[store_name_index + 1 :]
        if remaining:
            result.store_address = remaining[0]

    if result.total is None and result.subtotal is not None:
        # Some real receipts print only a "SUB TOTAL" line with no separate
        # "TOTAL" line at all (no tax/discount shown as its own line) —
        # confirmed against a real receipt. In that case the subtotal is
        # the actual grand total, not a partial figure awaiting adjustment.
        result.total = result.subtotal

    return result


_HEADER_LOOKAHEAD = 4
"""How many leading non-field lines to consider when picking the store name.
More than the two the parser needs (name and address), so a stray OCR
fragment above the real name does not push the address out of range."""

_MIN_STORE_NAME_LENGTH = 6
"""Shortest plausible printed store name. Real strays seen on receipts are
shorter than this ("RM", "X", "fire"); real names are longer."""


def _store_name_score(line: str) -> float:
    """How much this line looks like a printed store name rather than a
    stray OCR fragment. Confirmed against real receipts: a short fragment
    often appears directly above the real name ("RM" above "ROTAMEDIC GRA
    OFFICE"), so taking the first non-field line blindly picks the stray.

    A printed store sign is set in large type, so OCR reads it as a longer
    run of mostly letters, usually capitalised. A stray is short, or heavy
    in digits and punctuation. This cannot repair a name OCR misread
    outright ("SUPREME PHARMACY & STORI") — only prefer the better of the
    candidate lines actually present."""
    stripped = line.strip()
    if len(stripped) < _MIN_STORE_NAME_LENGTH:
        return 0.0

    letters = sum(character.isalpha() for character in stripped)
    if letters == 0:
        return 0.0

    letter_ratio = letters / len(stripped)
    uppercase_letters = sum(
        character.isupper() for character in stripped if character.isalpha()
    )
    uppercase_ratio = uppercase_letters / letters

    # Length helps up to a point: a full address line is longer than a store
    # name but should not outscore it, so the length term saturates.
    length_score = min(len(stripped), 30) / 30
    return letter_ratio * 2 + uppercase_ratio + length_score


def _pick_store_name_index(candidates: list[str]) -> int | None:
    """Index of the candidate line that best looks like a store name, or
    None when no candidate is plausible. Ties keep the earliest line, since
    the store name is normally printed first."""
    best_index: int | None = None
    best_score = 0.0
    for index, candidate in enumerate(candidates):
        score = _store_name_score(candidate)
        if score > best_score:
            best_index = index
            best_score = score
    return best_index


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


_PAYMENT_LINE = re.compile(
    r"\b(?:cash|change|tendered|balance|card|pos|transfer|moniepoint|opay|"
    r"paystack|flutterwave|bank|paid|payment)\b|\bnet\s*tot",
    re.IGNORECASE,
)
"""Lines naming how the customer paid, or a total under a label the four
patterns above miss. Confirmed against real receipts: a "CASH 8,800.00"
payment line and a "Net Totnl 9,550.00" line (OCR of "Net Total") were both
parsed as purchased items, each duplicating the receipt total as an extra
line item. `net tot` is deliberately truncated to survive that OCR misread
of the trailing "al"."""

_TOTALS_LINE_PATTERNS = (
    _TOTAL_LINE,
    _SUBTOTAL_LINE,
    _TAX_LINE,
    _DISCOUNT_LINE,
    _PAYMENT_LINE,
)


def _parse_two_line_item(line: str, next_line: str | None) -> ParsedLineItem | None:
    """Matches an item printed across two physical lines: a numbered
    description, then its quantity and prices. Returns None unless BOTH
    halves match, so a numbered line that is not followed by a quantity
    line falls through to the normal single-line handling."""
    if next_line is None:
        return None

    description_match = _ITEM_NUMBERED_DESCRIPTION.match(line)
    if description_match is None:
        return None

    qty_match = _ITEM_QTY_PRICE_LINE.match(next_line)
    if qty_match is None:
        return None

    quantity = float(qty_match.group("quantity"))
    unit_price = float(qty_match.group(2).replace(",", ""))
    line_total_raw = qty_match.group(3)
    line_total = (
        float(line_total_raw.replace(",", ""))
        if line_total_raw
        else round(quantity * unit_price, 2)
    )
    return ParsedLineItem(
        description=description_match.group("description").strip(),
        quantity=quantity,
        unit_price=unit_price,
        line_total=line_total,
    )


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
