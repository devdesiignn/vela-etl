"""Orchestrator: runs a config-driven list of extractors against one image.

Per DESIGN-V3.md's Extract section: extractors are a list, not a special-cased
"one vs. many" branch. The same loop handles 1 or N, and the orchestrator
never branches on which extractor ran or why.

split_results() does the partition tests/test_transform.py's
test_reconcile_only_receives_extraction_results_not_total_failure_rows
describes as "the orchestrator's job": reconcile() only ever operates on
ExtractionResults, so a total-failure ExtractionReview must be routed to the
final review list before Transform sees the results.
"""

from __future__ import annotations

from etl.extract.protocol import Extractor, Image
from etl.extract.types import ExtractionResult
from etl.types.extraction_review_schema import ExtractionReview


def run_extractors(
    extractors: list[Extractor], image: Image
) -> list[ExtractionResult | ExtractionReview]:
    return [extractor.extract(image) for extractor in extractors]


def split_results(
    results: list[ExtractionResult | ExtractionReview],
) -> tuple[list[ExtractionResult], list[ExtractionReview]]:
    extraction_results = [r for r in results if isinstance(r, ExtractionResult)]
    reviews = [r for r in results if isinstance(r, ExtractionReview)]
    return extraction_results, reviews
