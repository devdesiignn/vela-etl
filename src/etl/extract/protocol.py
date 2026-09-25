"""Extractor interface, per DESIGN-V3.md's Extract section.

All extractor kinds (OCR, vision LLM, manual) implement extract(image) ->
ExtractionResult. On total extraction failure (unreadable image, API error),
an extractor returns an ExtractionReview-shaped row directly instead, since
there's no candidate to reconcile.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID, uuid4

from etl.extract.types import ExtractionResult
from etl.types.extraction_review_schema import ExtractionReview, FlaggedReason, Status

Image = bytes


class Extractor(Protocol):
    def extract(self, image: Image) -> ExtractionResult | ExtractionReview: ...


def confidence_map(scores: dict[str, float | None]) -> dict[str, float]:
    """Build a {field: score} confidence dict, dropping fields whose score
    is None (field not found/reported by the extractor)."""
    return {field: score for field, score in scores.items() if score is not None}


def extraction_failed_review(
    *, receipt_id: UUID, source: str, notes: str
) -> ExtractionReview:
    """Build the total-extraction-failure ExtractionReview row every
    extractor returns instead of raising (unreadable image, vendor API
    error, or any case with no candidate to reconcile)."""
    return ExtractionReview(
        id=uuid4(),
        receipt_id=receipt_id,
        line_item_id=None,
        field_name="receipt",
        extractor_source=source,
        extracted_value=None,
        confidence_score=0.0,
        flagged_reason=FlaggedReason.extraction_failed,
        extractor_notes=notes,
        status=Status.pending,
        reviewed_value=None,
    )
