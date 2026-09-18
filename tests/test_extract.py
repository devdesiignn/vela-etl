from typing import Any
from uuid import UUID, uuid4

from etl.extract import (
    Candidate,
    CandidateLineItem,
    CandidateReceipt,
    CandidateStore,
    Confidence,
    ExtractionResult,
    run_extractors,
    split_results,
)
from etl.extract.protocol import Image
from etl.types.extraction_review_schema import (
    ExtractionReview,
    FlaggedReason,
    Status,
)

RECEIPT_ID = UUID("44d21dc0-ada2-4b24-9ad4-91800c4922a9")
IMAGE = b"stub-image-bytes"

STORE_FIELDS: dict[str, Any] = {
    "name": "Supreme Pharmacy",
    "address": "12 Ikorodu Rd, Lagos",
}
RECEIPT_FIELDS: dict[str, Any] = {
    "source_image_id": "img_001",
    "transaction_ref": "INV-2026-0042",
    "date": "2026-01-15",
    "total": 300.0,
}
LINE_ITEM: dict[str, Any] = {
    "description": "Paracetamol 500mg",
    "quantity": 2,
    "unit_price": 150.0,
    "line_total": 300.0,
}


def _stub_result(source: str) -> ExtractionResult:
    return ExtractionResult(
        source=source,
        candidate=Candidate(
            store=CandidateStore(**STORE_FIELDS),
            receipt=CandidateReceipt(**RECEIPT_FIELDS),
            line_items=[CandidateLineItem(**LINE_ITEM)],
        ),
        confidence=Confidence(store={}, receipt={}, line_items=[{}]),
    )


class _StubExtractor:
    def __init__(self, source: str) -> None:
        self._source = source

    def extract(self, image: Image) -> ExtractionResult:
        assert image == IMAGE
        return _stub_result(self._source)


class _TotalFailureExtractor:
    def __init__(self, source: str = "stub-failure") -> None:
        self._source = source

    def extract(self, image: Image) -> ExtractionReview:
        assert image == IMAGE
        return ExtractionReview(
            id=uuid4(),
            receipt_id=RECEIPT_ID,
            line_item_id=None,
            # field_name="receipt" + flagged_reason="extraction_failed": the
            # total-extraction-failure sentinel added in vela-core commit
            # 1619173, per docs/DECISIONS.md's "Total-failure ExtractionReview
            # shape" entry.
            field_name="receipt",
            extractor_source=self._source,
            extracted_value=None,
            confidence_score=0.0,
            flagged_reason=FlaggedReason.extraction_failed,
            status=Status.pending,
            reviewed_value=None,
        )


def test_run_extractors_single():
    results = run_extractors([_StubExtractor("extractor-a")], IMAGE)

    assert len(results) == 1
    assert isinstance(results[0], ExtractionResult)
    assert results[0].source == "extractor-a"


def test_run_extractors_multiple_same_shape_as_single():
    results = run_extractors(
        [_StubExtractor("extractor-a"), _StubExtractor("extractor-b")], IMAGE
    )

    assert [r.source for r in results if isinstance(r, ExtractionResult)] == [
        "extractor-a",
        "extractor-b",
    ]


def test_run_extractors_empty_list():
    assert run_extractors([], IMAGE) == []


def test_run_extractors_passes_through_total_failure_review_unchanged():
    results = run_extractors(
        [_StubExtractor("extractor-a"), _TotalFailureExtractor()], IMAGE
    )

    assert len(results) == 2
    assert isinstance(results[0], ExtractionResult)
    assert isinstance(results[1], ExtractionReview)
    assert results[1].flagged_reason == FlaggedReason.extraction_failed


def test_split_results_separates_extraction_results_from_reviews():
    results = run_extractors(
        [_StubExtractor("extractor-a"), _TotalFailureExtractor()], IMAGE
    )

    extraction_results, reviews = split_results(results)

    assert len(extraction_results) == 1
    assert extraction_results[0].source == "extractor-a"
    assert len(reviews) == 1
    assert reviews[0].flagged_reason == FlaggedReason.extraction_failed


def test_split_results_empty():
    assert split_results([]) == ([], [])


def test_run_extractors_multiple_total_failures_all_collected():
    results = run_extractors(
        [_TotalFailureExtractor("extractor-a"), _TotalFailureExtractor("extractor-b")],
        IMAGE,
    )

    assert len(results) == 2
    reviews = [r for r in results if isinstance(r, ExtractionReview)]
    assert len(reviews) == 2
    assert [r.extractor_source for r in reviews] == ["extractor-a", "extractor-b"]


def test_run_extractors_all_fail_no_extraction_results():
    results = run_extractors(
        [_TotalFailureExtractor("extractor-a"), _TotalFailureExtractor("extractor-b")],
        IMAGE,
    )

    extraction_results, reviews = split_results(results)

    assert extraction_results == []
    assert len(reviews) == 2
    assert [r.extractor_source for r in reviews] == ["extractor-a", "extractor-b"]


def test_run_extractors_mixed_successes_and_failures():
    results = run_extractors(
        [
            _StubExtractor("extractor-a"),
            _TotalFailureExtractor("extractor-b"),
            _StubExtractor("extractor-c"),
            _TotalFailureExtractor("extractor-d"),
        ],
        IMAGE,
    )

    extraction_results, reviews = split_results(results)

    assert [r.source for r in extraction_results] == ["extractor-a", "extractor-c"]
    assert [r.extractor_source for r in reviews] == ["extractor-b", "extractor-d"]
