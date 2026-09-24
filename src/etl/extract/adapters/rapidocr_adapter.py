"""RapidOCR adapter: raw OCR text in, Candidate shape out.

RapidOCR gives no receipt structure and no field-level confidence usable at
the field level — only per-text-box detection confidence, which doesn't map
onto "is this the date field" or "is this the total." Per DESIGN-V3.md's
three confidence origins, this adapter falls back entirely to derived
confidence: quantity * unit_price == line_total for line items, and "was
this field found at all" for everything else. confidence_score is a
required non-null column downstream, so every field this adapter reports
must get some score.

Total extraction failure (unreadable image, empty OCR output, no total and
no line items recovered) returns an ExtractionReview row directly instead of
raising, per the Extractor Protocol. CandidateReceipt makes date a required,
non-optional field, so it has no sentinel value for a missing receipt date.
When OCR finds no date, this adapter treats that alone as a total failure
rather than guessing.

Runs RapidOCR twice per image, once on the raw bytes and once on
preprocess()'d bytes, and keeps whichever parse recovers more fields.
Confirmed against real receipts: preprocess()'s deskew/contrast/threshold
pipeline (tuned originally for Tesseract) sometimes corrupts RapidOCR's
read of a clean, well-lit photo, and sometimes rescues a poorly-lit or
skewed one RapidOCR misreads raw. Neither input reliably wins across real
photos, so this adapter tries both rather than committing to one.

RapidOCR detects each distinct text region as its own separate box, even
when several sit on the same physical printed line (e.g. "SUB TOTAL" and
"8,800.00" come back as two separate strings, not one "SUB TOTAL  8,800.00"
line). ocr_text_parser.py's regexes assume a label and its value share one
line, the way Tesseract's block-text output naturally groups them.
_group_boxes_into_lines() below reconstructs that assumption from RapidOCR's
box geometry (its (x, y) corner coordinates) before handing text to the
existing parser: boxes are grouped into rows by vertical overlap, then
ordered left-to-right within a row and joined with spaces.

RapidOCR replaced an earlier pytesseract-based adapter (see
docs/DECISIONS.md's "OCR engine: RapidOCR, not pytesseract/Tesseract"
entry): pytesseract needs the tesseract-ocr binary installed as a separate
OS package on every machine that runs this code, with no way to pin or
bundle it via uv. RapidOCR is a plain PyPI package with no such system
dependency, matching this repo's "install one thing, get a working
pipeline" convention for everything else. RapidOCR's own model files (its
ONNX weights) are cached under the package install after the first run,
not fetched fresh on every call.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import numpy as np
from PIL import UnidentifiedImageError
from rapidocr import RapidOCR
from rapidocr.utils.output import RapidOCROutput

from etl.extract.adapters.ocr_text_parser import (
    ParsedLineItem,
    ParsedReceipt,
    parse,
)
from etl.extract.preprocess import normalize_orientation, preprocess, rotate
from etl.extract.protocol import Image, confidence_map, extraction_failed_review
from etl.extract.types import (
    Candidate,
    CandidateLineItem,
    CandidateReceipt,
    CandidateStore,
    Confidence,
    ExtractionResult,
)
from etl.types.extraction_review_schema import ExtractionReview

SOURCE = "rapidocr"

_FOUND_CONFIDENCE = 0.6
"""A found-but-unvalidated field: better than nothing, well short of a
validated/native score, since regex extraction can mismatch on messy OCR
text. Named constant since every "field present" case here uses it."""

_ARITHMETIC_MISMATCH_CONFIDENCE = 0.2
_ARITHMETIC_TOLERANCE = 0.01

_RETRY_ROTATIONS = (90, 180, 270)
"""Quarter turns tried when a photo looks sideways. A receipt is rectangular
and printed one way up, so only right angles are worth trying."""

_ROTATION_MAX_LINES = 12
_ROTATION_MIN_AVERAGE_LENGTH = 45.0
"""Signature of a sideways photo, measured across 28 real receipts: upright
ones give 18-40 grouped lines averaging 18-28 characters, sideways ones give
4-9 lines averaging 59-90. Both conditions must hold, so a short receipt
with normal-length lines does not trigger a retry."""

_RECONCILIATION_TOLERANCE = 0.01
"""How far the line-item sum may sit from the receipt total before the parse
counts as partial. Kept at one cent: real receipts balance exactly, and a
wider tolerance would admit a genuinely missed item on a low-value line."""

_engine = RapidOCR()
"""Module-level singleton: RapidOCR() loads its ONNX models at construction
time, an expensive one-time cost this adapter should pay once per process,
not once per receipt."""


class RapidOcrAdapter:
    def __init__(self, receipt_id: UUID | None = None) -> None:
        # A local placeholder id: at extract time no receipt row exists yet in
        # vela-core (Load hasn't run), so this only shapes a total-failure
        # ExtractionReview, not a real foreign key.
        self._receipt_id = receipt_id or uuid4()

    def extract(self, image: Image) -> ExtractionResult | ExtractionReview:
        try:
            oriented = normalize_orientation(image)
        except UnidentifiedImageError:
            return self._failure_review("could not decode image")

        candidates: list[ParsedReceipt] = []

        raw_parsed, raw_lines = self._run_ocr_with_lines(oriented)
        if raw_parsed is not None:
            candidates.append(raw_parsed)

        try:
            # preprocess() normalizes orientation itself; pass the original
            # bytes, not `oriented`, to avoid correcting it twice.
            cleaned = preprocess(image)
        except ValueError:
            cleaned = None
        if cleaned is not None:
            preprocessed_parsed = self._run_ocr(cleaned)
            if preprocessed_parsed is not None:
                candidates.append(preprocessed_parsed)

        # A photo taken sideways still OCRs legibly, but the box geometry is
        # rotated, so _group_boxes_into_lines() reads across the receipt
        # instead of down it. The result collapses into a few very long
        # lines. Measured across 28 real photos: upright receipts give 18-40
        # lines averaging 18-28 characters, while sideways ones give 4-9
        # lines averaging 59-90. Retrying the quarter turns only when that
        # signature appears keeps the extra OCR cost off normal receipts.
        #
        # Each rotation only ever adds another candidate. The best-parse
        # selection below still decides, so a rotation that reads worse is
        # ignored rather than preferred.
        if _looks_rotated(raw_lines):
            for degrees in _RETRY_ROTATIONS:
                try:
                    turned = rotate(oriented, degrees)
                except (UnidentifiedImageError, OSError):
                    continue
                rotated_parsed = self._run_ocr(turned)
                if rotated_parsed is not None:
                    candidates.append(rotated_parsed)

        if not candidates:
            return self._failure_review(
                "could not decode image, or OCR returned no text from either "
                "the raw or the preprocessed version"
            )

        parsed = max(candidates, key=_recovered_field_count)
        if parsed.total is None and not parsed.line_items:
            return self._failure_review(
                "no total or line items recovered from OCR text"
            )
        if parsed.date is None:
            return self._failure_review("no receipt date recovered from OCR text")
        # CandidateReceipt.total is non-nullable, so a missing total can only
        # be reported downstream as 0.0 — indistinguishable from a genuinely
        # zero-value receipt. Confirmed against real receipts: 7 of 28 in the
        # sample recovered line items but no total, and every one shipped as a
        # successful extraction carrying total=0.0. A receipt whose line items
        # parsed but whose total did not is a partial read, not a success.
        if parsed.total is None:
            return self._failure_review(
                "line items recovered but no total — total would be "
                "indistinguishable from a real 0.00 receipt"
            )
        # Same reasoning in reverse: a total with no line items at all is a
        # partial read. 4 of 28 real receipts hit this, each reporting a store
        # and total with items=0 while the printed receipt plainly had items.
        if not parsed.line_items:
            return self._failure_review(
                "total recovered but no line items parsed from OCR text"
            )
        # A total that parsed as literal 0.00 alongside real line items is a
        # misparse, not a free receipt — confirmed on 2 of 28 real receipts,
        # one carrying 16 line items against a 0.00 total. This is a separate
        # path from the missing-total case above: the value was found and read
        # as zero, so an `is None` check never sees it.
        if parsed.total == 0.0:
            return self._failure_review(
                "total parsed as 0.00 alongside line items — a misread total, "
                "not a zero-value receipt"
            )
        # The gates above detect an absent field. This one detects a partial
        # read: line items that do not add up to the total mean the parser
        # missed an item, invented one, or misread an amount. Arithmetic is
        # the only signal available for "how many items should there be,"
        # since nothing else states the expected count.
        #
        # Receipts that show tax or a discount legitimately break this
        # identity, so those adjust the expected sum. No receipt in the real
        # 28-photo sample carried either, so that path is reasoned from the
        # schema rather than confirmed against a photo.
        item_sum = round(sum(item.line_total for item in parsed.line_items), 2)
        expected = parsed.total - (parsed.tax or 0.0) + (parsed.discount or 0.0)
        if abs(item_sum - round(expected, 2)) > _RECONCILIATION_TOLERANCE:
            return self._failure_review(
                f"line items sum to {item_sum:.2f}, which does not reconcile "
                f"with the receipt total of {parsed.total:.2f} — the parse "
                "missed, invented, or misread at least one item"
            )

        return self._to_extraction_result(parsed)

    def _run_ocr(self, image: Image) -> ParsedReceipt | None:
        parsed, _ = self._run_ocr_with_lines(image)
        return parsed

    def _run_ocr_with_lines(
        self, image: Image
    ) -> tuple[ParsedReceipt | None, list[str]]:
        """Parsed receipt plus the grouped lines it came from. The caller
        uses the lines to test for a sideways photo without paying for
        another OCR pass over the same image."""
        try:
            ocr_result = _engine(image)
        except UnidentifiedImageError:
            return None, []
        if not isinstance(ocr_result, RapidOCROutput):
            # _engine() is always called with det/cls/rec all enabled (the
            # defaults), so this only guards against a future config change
            # that narrows the pipeline and changes the return type.
            return None, []

        if not ocr_result.txts or ocr_result.boxes is None:
            return None, []

        lines = _group_boxes_into_lines(ocr_result.boxes, ocr_result.txts)
        raw_text = "\n".join(lines)
        if not raw_text.strip():
            return None, lines

        return parse(raw_text), lines

    def _failure_review(self, notes: str) -> ExtractionReview:
        return extraction_failed_review(
            receipt_id=self._receipt_id, source=SOURCE, notes=notes
        )

    def _to_extraction_result(self, parsed: ParsedReceipt) -> ExtractionResult:
        assert parsed.date is not None  # validated by extract() before calling this

        store = CandidateStore(
            name=parsed.store_name or "",
            address=parsed.store_address or "",
            phone=parsed.phone,
            email=parsed.email,
            website=parsed.website,
        )
        store_confidence = confidence_map(
            {
                field: _FOUND_CONFIDENCE if getattr(store, field) else None
                for field in ("name", "address", "phone", "email", "website")
            }
        )

        candidate_items: list[CandidateLineItem] = []
        line_item_confidence: list[dict[str, float]] = []
        for item in parsed.line_items:
            candidate_items.append(
                CandidateLineItem(
                    description=item.description,
                    quantity=item.quantity,
                    unit_price=item.unit_price,
                    line_total=item.line_total,
                )
            )
            line_item_confidence.append(_derived_line_item_confidence(item))

        receipt = CandidateReceipt(
            source_image_id="",
            transaction_ref=parsed.transaction_ref or "",
            date=parsed.date,
            subtotal=parsed.subtotal,
            discount=parsed.discount,
            vat=parsed.tax,
            total=parsed.total if parsed.total is not None else 0.0,
            line_items=candidate_items or None,
        )
        receipt_confidence = confidence_map(
            {
                field: _FOUND_CONFIDENCE if found else None
                for field, found in (
                    ("transaction_ref", bool(parsed.transaction_ref)),
                    ("date", True),
                    ("subtotal", parsed.subtotal is not None),
                    ("discount", parsed.discount is not None),
                    ("vat", parsed.tax is not None),
                    ("total", parsed.total is not None),
                )
            }
        )

        return ExtractionResult(
            source=SOURCE,
            candidate=Candidate(
                store=store, receipt=receipt, line_items=candidate_items
            ),
            confidence=Confidence(
                store=store_confidence,
                receipt=receipt_confidence,
                line_items=line_item_confidence,
            ),
        )


def _group_boxes_into_lines(boxes: np.ndarray, txts: tuple[str, ...]) -> list[str]:
    """Merges RapidOCR's per-text-box detections into physical printed
    lines, ordered top-to-bottom then left-to-right, so a label ("SUB
    TOTAL") and its value ("8,800.00") end up on the same joined line even
    though RapidOCR reported them as separate boxes. Each box is `boxes[i]`,
    a 4x2 array of its four corner (x, y) points; `txts[i]` is its text.

    Rows are found by sorting boxes by vertical center and starting a new
    row whenever the gap to the previous box's center exceeds half the
    receipt's median box height. This threshold scales with the image
    itself rather than any fixed pixel count, so it works across different
    photo resolutions.

    An earlier version grew each row's vertical span to cover every box
    that joined it (row_bottom = max(row_bottom, new_box_bottom)). That let
    a single outlier box — one with an unusually large detected height,
    confirmed on a real receipt (one box spanning 1377px against a 316px
    median) — swallow the row's threshold and merge in every subsequent
    box regardless of its actual position, scrambling the whole receipt
    into one garbled line. Comparing each box only to the previous box's
    center, not to an ever-growing row boundary, avoids that failure mode.
    """
    if len(boxes) == 0:
        return []

    tops = boxes[:, :, 1].min(axis=1)
    bottoms = boxes[:, :, 1].max(axis=1)
    lefts = boxes[:, :, 0].min(axis=1)
    centers = (tops + bottoms) / 2
    heights = bottoms - tops

    median_height = float(np.median(heights))
    row_gap_threshold = median_height / 2 if median_height > 0 else 1.0

    order = np.argsort(centers)

    rows: list[list[int]] = []
    previous_center: float | None = None
    for i in order:
        center = float(centers[i])
        if previous_center is None or center - previous_center > row_gap_threshold:
            rows.append([i])
        else:
            rows[-1].append(i)
        previous_center = center

    lines: list[str] = []
    for row in rows:
        row.sort(key=lambda i: lefts[i])
        lines.append(" ".join(txts[i] for i in row))
    return lines


def _looks_rotated(lines: list[str]) -> bool:
    """True when OCR grouped into few, unusually long lines — the signature
    of a photo taken sideways, where the grouping reads across the receipt
    rather than down it."""
    if not lines or len(lines) > _ROTATION_MAX_LINES:
        return False

    average_length = sum(len(line) for line in lines) / len(lines)
    return average_length >= _ROTATION_MIN_AVERAGE_LENGTH


def _recovered_field_count(parsed: ParsedReceipt) -> int:
    """How many receipt-level fields plus line items this parse recovered.
    Used to pick the better of the raw-bytes and preprocess()'d parses:
    whichever OCR pass gave the text parser more to work with, per receipt,
    since neither input reliably reads better than the other."""
    scalar_fields = (
        parsed.store_name,
        parsed.store_address,
        parsed.phone,
        parsed.email,
        parsed.website,
        parsed.transaction_ref,
        parsed.date,
        parsed.subtotal,
        parsed.discount,
        parsed.tax,
        parsed.total,
    )
    return sum(field is not None for field in scalar_fields) + len(parsed.line_items)


def _derived_line_item_confidence(item: ParsedLineItem) -> dict[str, float]:
    expected = round(item.quantity * item.unit_price, 2)
    gap = abs(expected - item.line_total)
    score = (
        _FOUND_CONFIDENCE
        if gap <= _ARITHMETIC_TOLERANCE
        else _ARITHMETIC_MISMATCH_CONFIDENCE
    )
    return {
        "description": _FOUND_CONFIDENCE,
        "quantity": score,
        "unit_price": score,
        "line_total": score,
    }
