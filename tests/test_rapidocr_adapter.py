from unittest.mock import patch
from uuid import UUID

import cv2
import numpy as np
from rapidocr.utils.output import RapidOCROutput

from etl.extract.adapters.rapidocr_adapter import (
    RapidOcrAdapter,
    _group_boxes_into_lines,
    _looks_rotated,
)
from etl.extract.types import ExtractionResult
from etl.types.extraction_review_schema import ExtractionReview, FlaggedReason

RECEIPT_ID = UUID("44d21dc0-ada2-4b24-9ad4-91800c4922a9")

CLEAN_TEXT_LINES = (
    "Supreme Pharmacy",
    "12 Ikorodu Rd, Lagos",
    "Date: 2026-01-15",
    "Invoice #INV-2026-0042",
    "Paracetamol 500mg 2 x 150.00 = 300.00",
    "Total 300.00",
)

MISMATCHED_ITEM_LINES = (
    "Corner Store",
    "5 Allen Ave, Ikeja",
    "2026-01-15",
    "Bread 3 x 200.00 = 999.00",
    "Total 999.00",
)


def _valid_image() -> bytes:
    img = np.full((100, 200, 3), 255, dtype=np.uint8)
    ok, buf = cv2.imencode(".png", img)
    assert ok
    return buf.tobytes()


def _mock_ocr_result(txts: tuple[str, ...] | None) -> RapidOCROutput:
    """Builds a RapidOCROutput with one box per string in `txts`, each on
    its own row (stacked vertically, 100px apart) — the adapter's
    _group_boxes_into_lines() needs real box geometry to reconstruct
    physical lines, and every existing fixture here already represents
    one string as one intended physical line."""
    if txts is None:
        return RapidOCROutput(txts=None, boxes=None)
    boxes = np.array(
        [
            [[0, i * 100], [200, i * 100], [200, i * 100 + 50], [0, i * 100 + 50]]
            for i in range(len(txts))
        ],
        dtype=np.float64,
    )
    return RapidOCROutput(txts=txts, boxes=boxes)


def test_group_boxes_into_lines_merges_same_row_boxes():
    """RapidOCR detects 'SUB TOTAL' and '8,800.00' as two separate boxes
    even when they sit on the same physical printed line. Boxes with
    overlapping vertical spans (same row) must merge into one line,
    ordered left-to-right; boxes on different rows stay separate lines."""
    boxes = np.array(
        [
            # Row 0: "SUB TOTAL" (left) and "8,800.00" (right), same y-span.
            [[0, 0], [100, 0], [100, 30], [0, 30]],
            [[150, 2], [250, 2], [250, 32], [150, 32]],
            # Row 1: a single box, well below row 0.
            [[0, 100], [80, 100], [80, 130], [0, 130]],
        ],
        dtype=np.float64,
    )
    txts = ("SUB TOTAL", "8,800.00", "CASH")

    lines = _group_boxes_into_lines(boxes, txts)

    assert lines == ["SUB TOTAL 8,800.00", "CASH"]


def test_group_boxes_into_lines_orders_rows_top_to_bottom():
    boxes = np.array(
        [
            [[0, 200], [80, 200], [80, 230], [0, 230]],  # appears last in input
            [[0, 0], [80, 0], [80, 30], [0, 30]],  # appears first in input
        ],
        dtype=np.float64,
    )
    txts = ("Second row", "First row")

    lines = _group_boxes_into_lines(boxes, txts)

    assert lines == ["First row", "Second row"]


def test_extract_prefers_whichever_pass_recovers_more_fields():
    """preprocess() sometimes helps RapidOCR's read, sometimes hurts it,
    confirmed against real receipts (see rapidocr_adapter.py's module
    docstring). The adapter must pick whichever of the raw-bytes pass or
    the preprocess()'d pass recovers more fields, not always the same one.
    """
    adapter = RapidOcrAdapter(receipt_id=RECEIPT_ID)

    incomplete_lines = ("Corner Store", "Total 300.00")  # no date, no items
    call_count = 0

    def fake_engine(_image: bytes) -> RapidOCROutput:
        nonlocal call_count
        call_count += 1
        # First call = raw bytes (incomplete), second = preprocess()'d
        # bytes (complete) — the adapter must keep the second, not the
        # first-seen result.
        return _mock_ocr_result(
            incomplete_lines if call_count == 1 else CLEAN_TEXT_LINES
        )

    with patch(
        "etl.extract.adapters.rapidocr_adapter._engine", side_effect=fake_engine
    ):
        result = adapter.extract(_valid_image())

    assert isinstance(result, ExtractionResult)
    assert result.candidate.store.name == "Supreme Pharmacy"
    assert result.candidate.receipt.total == 300.00


def test_extract_returns_extraction_result_for_clean_text():
    adapter = RapidOcrAdapter(receipt_id=RECEIPT_ID)
    with patch("etl.extract.adapters.rapidocr_adapter._engine") as mock_engine:
        mock_engine.return_value = _mock_ocr_result(CLEAN_TEXT_LINES)
        result = adapter.extract(_valid_image())

    assert isinstance(result, ExtractionResult)
    assert result.source == "rapidocr"
    assert result.candidate.store.name == "Supreme Pharmacy"
    assert result.candidate.receipt.total == 300.00
    assert len(result.candidate.line_items) == 1
    assert result.confidence.receipt["total"] > 0


def test_extract_scores_arithmetic_mismatch_lower_than_clean_match():
    adapter = RapidOcrAdapter(receipt_id=RECEIPT_ID)

    with patch("etl.extract.adapters.rapidocr_adapter._engine") as mock_engine:
        mock_engine.return_value = _mock_ocr_result(CLEAN_TEXT_LINES)
        clean_result = adapter.extract(_valid_image())

    with patch("etl.extract.adapters.rapidocr_adapter._engine") as mock_engine:
        mock_engine.return_value = _mock_ocr_result(MISMATCHED_ITEM_LINES)
        mismatched_result = adapter.extract(_valid_image())

    assert isinstance(clean_result, ExtractionResult)
    assert isinstance(mismatched_result, ExtractionResult)
    clean_score = clean_result.confidence.line_items[0]["line_total"]
    mismatched_score = mismatched_result.confidence.line_items[0]["line_total"]
    assert mismatched_score < clean_score


def test_extract_returns_review_on_undecodable_image():
    adapter = RapidOcrAdapter(receipt_id=RECEIPT_ID)

    result = adapter.extract(b"not an image")

    assert isinstance(result, ExtractionReview)
    assert result.receipt_id == RECEIPT_ID
    assert result.field_name == "receipt"
    assert result.flagged_reason == FlaggedReason.extraction_failed
    assert result.extracted_value is None
    assert result.confidence_score == 0.0


def test_extract_returns_review_on_empty_ocr_output():
    adapter = RapidOcrAdapter(receipt_id=RECEIPT_ID)

    with patch("etl.extract.adapters.rapidocr_adapter._engine") as mock_engine:
        mock_engine.return_value = _mock_ocr_result(None)
        result = adapter.extract(_valid_image())

    assert isinstance(result, ExtractionReview)
    assert result.flagged_reason == FlaggedReason.extraction_failed


def test_extract_returns_review_when_no_total_or_line_items_found():
    adapter = RapidOcrAdapter(receipt_id=RECEIPT_ID)

    with patch("etl.extract.adapters.rapidocr_adapter._engine") as mock_engine:
        mock_engine.return_value = _mock_ocr_result(
            ("random", "unrecognizable", "garbage", "text")
        )
        result = adapter.extract(_valid_image())

    assert isinstance(result, ExtractionReview)
    assert result.flagged_reason == FlaggedReason.extraction_failed


def test_extract_returns_review_when_no_date_found():
    adapter = RapidOcrAdapter(receipt_id=RECEIPT_ID)

    with patch("etl.extract.adapters.rapidocr_adapter._engine") as mock_engine:
        mock_engine.return_value = _mock_ocr_result(("Store", "Total 300.00"))
        result = adapter.extract(_valid_image())

    assert isinstance(result, ExtractionReview)
    assert result.flagged_reason == FlaggedReason.extraction_failed


def test_extract_generates_placeholder_receipt_id_when_none_given():
    adapter = RapidOcrAdapter()

    result = adapter.extract(b"not an image")

    assert isinstance(result, ExtractionReview)
    assert isinstance(result.receipt_id, UUID)


def test_extract_returns_review_when_line_items_found_but_no_total():
    """Regression: 7 of 28 real sample receipts parsed line items but no
    total, and each shipped as a successful extraction carrying total=0.0,
    since CandidateReceipt.total is non-nullable and the old guard only
    rejected when total AND line items were both missing. A coerced 0.0 is
    indistinguishable downstream from a real zero-value receipt."""
    adapter = RapidOcrAdapter(receipt_id=RECEIPT_ID)

    with patch("etl.extract.adapters.rapidocr_adapter._engine") as mock_engine:
        mock_engine.return_value = _mock_ocr_result(
            (
                "Supreme Pharmacy",
                "Date: 2026-03-07",
                "Paracetamol 500mg 2 x 150.00 = 300.00",
            )
        )
        result = adapter.extract(_valid_image())

    assert isinstance(result, ExtractionReview)
    assert result.flagged_reason == FlaggedReason.extraction_failed


def test_extract_returns_review_when_total_found_but_no_line_items():
    """Regression: 4 of 28 real sample receipts recovered a store, date and
    total but zero line items, and reported success — despite the printed
    receipts plainly having items. A total with no items is a partial read."""
    adapter = RapidOcrAdapter(receipt_id=RECEIPT_ID)

    with patch("etl.extract.adapters.rapidocr_adapter._engine") as mock_engine:
        mock_engine.return_value = _mock_ocr_result(
            ("Mummy Ope General", "Date: 2026-02-17", "Total 7,900.00")
        )
        result = adapter.extract(_valid_image())

    assert isinstance(result, ExtractionReview)
    assert result.flagged_reason == FlaggedReason.extraction_failed


def test_extract_returns_review_when_total_parses_as_zero_with_line_items():
    """Regression: 2 of 28 real sample receipts parsed a literal 0.00 total
    alongside real line items (one with 16 items), and shipped as successful
    extractions. Distinct from the missing-total case: the value was found
    and read as zero, so an `is None` guard never sees it."""
    adapter = RapidOcrAdapter(receipt_id=RECEIPT_ID)

    with patch("etl.extract.adapters.rapidocr_adapter._engine") as mock_engine:
        mock_engine.return_value = _mock_ocr_result(
            (
                "Mamtess Mega Mall",
                "Date: 2026-09-04",
                "Paracetamol 500mg 2 x 150.00 = 300.00",
                "Total 0.00",
            )
        )
        result = adapter.extract(_valid_image())

    assert isinstance(result, ExtractionReview)
    assert result.flagged_reason == FlaggedReason.extraction_failed


def test_extract_returns_review_when_line_items_do_not_reconcile():
    """Regression: the other gates detect an absent field, never a partial
    read. A receipt whose line items do not sum to the total means the parse
    missed, invented, or misread an item."""
    adapter = RapidOcrAdapter(receipt_id=RECEIPT_ID)

    with patch("etl.extract.adapters.rapidocr_adapter._engine") as mock_engine:
        mock_engine.return_value = _mock_ocr_result(
            (
                "Corner Store",
                "Date: 2026-01-30",
                "Bread 1 x 200.00 = 200.00",
                "Total 500.00",
            )
        )
        result = adapter.extract(_valid_image())

    assert isinstance(result, ExtractionReview)
    assert result.flagged_reason == FlaggedReason.extraction_failed


def test_extract_accepts_line_items_that_reconcile_with_the_total():
    """The reconciliation gate must not reject a correct parse."""
    adapter = RapidOcrAdapter(receipt_id=RECEIPT_ID)

    with patch("etl.extract.adapters.rapidocr_adapter._engine") as mock_engine:
        mock_engine.return_value = _mock_ocr_result(
            (
                "Corner Store",
                "Date: 2026-01-30",
                "Bread 1 x 200.00 = 200.00",
                "Milk 2 x 150.00 = 300.00",
                "Total 500.00",
            )
        )
        result = adapter.extract(_valid_image())

    assert isinstance(result, ExtractionResult)
    assert result.candidate.receipt.total == 500.0


def test_looks_rotated_detects_few_long_lines():
    """A sideways photo groups into few, unusually long lines, because the
    grouping reads across the receipt rather than down it."""
    assert _looks_rotated(["x" * 100, "y" * 100, "z" * 100])


def test_looks_rotated_ignores_a_normal_upright_receipt():
    """Measured across 28 real photos: upright receipts give 18-40 lines
    averaging 18-28 characters. Those must not trigger a rotation retry."""
    assert not _looks_rotated(["Supreme Pharmacy", "Total 300.00"] * 12)


def test_looks_rotated_ignores_a_short_receipt_with_normal_lines():
    """Few lines alone is not the signature — the lines must also be long."""
    assert not _looks_rotated(["Corner Store", "2026-01-15", "Total 500.00"])
