"""Emit: combine reconcile + validate review rows, produce Load-ready output.

Per DESIGN-V3.md's "Transform pipeline" -> "4. Emit". No logic beyond
combining lists -- shaped receipt record, line items, full review-row list,
ready for Load.
"""

from __future__ import annotations

from dataclasses import dataclass

from etl.types.extraction_review_schema import ExtractionReview
from etl.types.line_item_schema import LineItem
from etl.types.receipt_schema import Receipt
from etl.types.store_schema import Store


@dataclass
class EmitResult:
    store: Store
    receipt: Receipt
    line_items: list[LineItem]
    extraction_reviews: list[ExtractionReview]


def emit(
    *,
    store: Store,
    receipt: Receipt,
    line_items: list[LineItem],
    reconcile_reviews: list[ExtractionReview],
    validate_reviews: list[ExtractionReview],
) -> EmitResult:
    return EmitResult(
        store=store,
        receipt=receipt,
        line_items=line_items,
        extraction_reviews=reconcile_reviews + validate_reviews,
    )
