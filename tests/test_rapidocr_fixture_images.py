"""End-to-end RapidOcrAdapter tests against committed synthetic receipt images.

Every other adapter test in this repo mocks the OCR engine and feeds the
parser hand-written strings, so none of them can catch a regression in the
real pipeline: EXIF handling, preprocess(), RapidOCR itself, box-to-line
grouping, and the text parser working together. These tests run that whole
chain against real PNGs committed under tests/fixtures/extract/, rendered by
tests/fixtures/receipt_images.py.

Two rules govern what is asserted here.

RapidOCR is real OCR, so character-level output is not contractual. It reads
"Milk" back as "Mi1k" on the qty fixture today. Assertions therefore stay on
what survives a character misread: a total parsing to the right number, the
right count of line items, a date parsing to the right date, money summing
correctly. Nothing asserts an exact description string.

The adapter must never run against several images in concurrent processes.
Doing so produced repeated "Unknown C++ exception from OpenCV code" failures
(see docs/DECISIONS.md), so these tests stay serial and share one cached OCR
pass per image via the module-level _extract() cache. Each OCR call is slow
on CPU, and the adapter runs the engine twice per call (raw and
preprocess()'d), so caching keeps the whole module to one pass per fixture.
"""

from __future__ import annotations

from datetime import date
from functools import cache
from pathlib import Path

import pytest

from etl.extract.adapters.rapidocr_adapter import RapidOcrAdapter
from etl.extract.types import ExtractionResult
from etl.types.extraction_review_schema import ExtractionReview

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "extract"


@cache
def _extract(name: str) -> ExtractionResult | ExtractionReview:
    """One real OCR pass per fixture image, shared by every test in this
    module. Cached because each call is slow and the adapter internally runs
    the engine twice (raw bytes, then preprocess()'d bytes)."""
    return RapidOcrAdapter().extract((FIXTURE_DIR / name).read_bytes())


def _result(name: str) -> ExtractionResult:
    result = _extract(name)
    assert isinstance(result, ExtractionResult), (
        f"{name} failed extraction entirely: {getattr(result, 'extractor_notes', None)}"
    )
    return result


def test_fixture_images_are_committed():
    """Guards the rest of this module: an empty fixture directory would
    otherwise make every test below fail with a confusing file-not-found."""
    assert sorted(p.name for p in FIXTURE_DIR.glob("*.png")) == [
        "clean_single_column.png",
        "qty_unit_price_items.png",
        "skewed.png",
        "subtotal_only.png",
    ]


def test_clean_receipt_extracts_totals_date_and_items():
    result = _result("clean_single_column.png")
    receipt = result.candidate.receipt

    assert receipt.date == date(2026, 1, 15)
    assert receipt.total == pytest.approx(2000.00)
    assert receipt.subtotal == pytest.approx(2000.00)
    assert len(result.candidate.line_items) == 3
    assert sum(
        item.line_total for item in result.candidate.line_items
    ) == pytest.approx(2000.00)


def test_clean_receipt_recovers_a_store_name():
    """The store name is the first non-field line of the receipt. OCR can
    misread characters inside it, so this asserts only that a plausible
    name came back, not its exact text."""
    store = _result("clean_single_column.png").candidate.store

    assert len(store.name) >= 4
    assert store.address


def test_qty_receipt_parses_quantity_and_unit_price_per_item():
    """The 'qty x unit_price = line_total' item form, which the no-qty
    fallback branch would otherwise collapse to quantity 1."""
    result = _result("qty_unit_price_items.png")
    items = result.candidate.line_items

    assert len(items) == 3
    assert result.candidate.receipt.total == pytest.approx(1350.00)
    # Quantities above 1 prove the qty branch matched, not the fallback.
    assert [item.quantity for item in items] == [2.0, 3.0, 4.0]
    for item in items:
        assert item.quantity * item.unit_price == pytest.approx(item.line_total)


def test_qty_receipt_scores_arithmetically_consistent_items_highly():
    """Every item line on this fixture satisfies qty * unit_price ==
    line_total, so the adapter's derived confidence must not fall to its
    arithmetic-mismatch score for any of them."""
    result = _result("qty_unit_price_items.png")

    for scores in result.confidence.line_items:
        assert scores["line_total"] > 0.5


def test_subtotal_only_receipt_falls_back_to_subtotal_as_total():
    """This receipt prints SUB TOTAL and no TOTAL line at all. The parser's
    fallback must promote the subtotal to the grand total, otherwise the
    adapter rejects the receipt for having no total."""
    receipt = _result("subtotal_only.png").candidate.receipt

    assert receipt.subtotal == pytest.approx(4000.00)
    assert receipt.total == pytest.approx(4000.00)
    assert receipt.date == date(2026, 3, 7)
    assert len(_result("subtotal_only.png").candidate.line_items) == 2


def test_skewed_receipt_still_extracts():
    """The page is rendered rotated 7 degrees. Getting a clean read back
    exercises preprocess()'s deskew step and the adapter's habit of keeping
    whichever of the raw and preprocessed passes recovers more fields."""
    result = _result("skewed.png")
    receipt = result.candidate.receipt

    assert receipt.date == date(2026, 4, 21)
    assert receipt.total == pytest.approx(1500.00)
    assert len(result.candidate.line_items) == 2


def test_every_fixture_reports_a_confidence_score_for_its_total():
    """confidence_score is a required non-null column downstream, so a
    recovered total must always carry a score."""
    for name in ("clean_single_column.png", "qty_unit_price_items.png", "skewed.png"):
        confidence = _result(name).confidence.receipt
        assert confidence["total"] > 0
        assert confidence["date"] > 0
