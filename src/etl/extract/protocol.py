"""Extractor interface, per DESIGN-V3.md's Extract section.

All extractor kinds (OCR, vision LLM, manual) implement extract(image) ->
ExtractionResult. On total extraction failure (unreadable image, API error),
an extractor returns an ExtractionReview-shaped row directly instead, since
there's no candidate to reconcile.
"""

from __future__ import annotations

from typing import Protocol

from etl.extract.types import ExtractionResult
from etl.types.extraction_review_schema import ExtractionReview

Image = bytes


class Extractor(Protocol):
    def extract(self, image: Image) -> ExtractionResult | ExtractionReview: ...
