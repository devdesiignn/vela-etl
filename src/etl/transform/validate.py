"""Validation: internal consistency checks on reconcile()'s resolved values.

Per DESIGN-V3.md's "Transform pipeline" -> "2. Validate": runs after reconcile,
only on resolved values. Checks quantity * unit_price == line_total, line items
sum to total/subtotal, required fields non-null. Produces review rows with
flagged_reason="validation_failed".
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from etl.types.extraction_review_schema import ExtractionReview, FlaggedReason, Status

_TOLERANCE = 0.01

# An absolute gap within this many multiples of _TOLERANCE is treated as
# negligible (near-full confidence) regardless of how small the total being
# compared against is -- e.g. a receipt with total=1.00 off by 0.02 is a
# rounding-scale gap in absolute terms, not a sign of a wrong value, even
# though 0.02/1.00 alone looks like a large relative error. This keeps the
# confidence score currency- and magnitude-agnostic: it reasons about the
# size of the gap itself (in the same tolerance unit already used for the
# pass/fail check above), never about the receipt's total.
_NEGLIGIBLE_GAP_MULTIPLE = 5

_REQUIRED_RECEIPT_FIELDS = ["total", "transaction_ref", "date", "source_image_id"]
_REQUIRED_LINE_ITEM_FIELDS = ["description", "quantity", "unit_price", "line_total"]
_REQUIRED_STORE_FIELDS = ["name", "address"]


def _mismatch_confidence(*, actual: float, expected: float) -> float:
    """Confidence that the flagged value is nonetheless correct.

    Blends two signals instead of relying on relative error alone:
    - Absolute gap size, in units of _TOLERANCE: a gap within
      _NEGLIGIBLE_GAP_MULTIPLE tolerances scores near-full confidence
      regardless of how small `expected` is, so a tiny total with a
      rounding-sized gap isn't scored as harshly as a wildly wrong one.
    - Relative error beyond that: once the absolute gap grows past the
      negligible range, confidence degrades by how large the gap is as a
      fraction of `expected` (or of the gap itself, if `expected` is 0).

    Floored at 0.0; capped below 1.0 (still 0.99) since this row is flagged
    regardless -- confidence can approach but never reach full trust.
    """
    absolute_gap = abs(actual - expected)
    negligible_threshold = _NEGLIGIBLE_GAP_MULTIPLE * _TOLERANCE
    if absolute_gap <= negligible_threshold:
        return 0.99

    reference = abs(expected) if expected != 0 else absolute_gap
    relative_error = absolute_gap / reference
    return max(0.0, min(0.99, 1.0 - relative_error))


def _validation_row(
    *,
    receipt_id: UUID,
    field_name: str,
    line_item_id: UUID | None = None,
    confidence_score: float = 0.0,
) -> ExtractionReview:
    return ExtractionReview(
        id=uuid4(),
        receipt_id=receipt_id,
        line_item_id=line_item_id,
        field_name=field_name,
        extractor_source="validation",
        extracted_value=None,
        # Derived-by-validation confidence (design doc's origin #3): always
        # available, used as the required-column fallback. A missing required
        # field has no "how far off" to measure, so it stays 0.0 (certain).
        # An arithmetic mismatch scales via _mismatch_confidence instead.
        confidence_score=confidence_score,
        flagged_reason=FlaggedReason.validation_failed,
        status=Status.pending,
    )


def validate(resolved: dict[str, Any], *, receipt_id: UUID) -> list[ExtractionReview]:
    """Check internal consistency of reconcile()'s resolved candidate values."""
    reviews: list[ExtractionReview] = []

    store: dict[str, Any] = resolved.get("store", {})
    receipt: dict[str, Any] = resolved.get("receipt", {})
    line_items: list[dict[str, Any]] = resolved.get("line_items", [])

    for field_name in _REQUIRED_STORE_FIELDS:
        if store.get(field_name) in (None, ""):
            reviews.append(
                _validation_row(receipt_id=receipt_id, field_name=field_name)
            )

    for field_name in _REQUIRED_RECEIPT_FIELDS:
        if receipt.get(field_name) in (None, ""):
            reviews.append(
                _validation_row(receipt_id=receipt_id, field_name=field_name)
            )

    for item in line_items:
        for field_name in _REQUIRED_LINE_ITEM_FIELDS:
            if item.get(field_name) in (None, ""):
                reviews.append(
                    _validation_row(receipt_id=receipt_id, field_name=field_name)
                )

        quantity: float | None = item.get("quantity")
        unit_price: float | None = item.get("unit_price")
        line_total: float | None = item.get("line_total")
        if quantity is not None and unit_price is not None and line_total is not None:
            expected_total = quantity * unit_price
            if abs(expected_total - line_total) >= _TOLERANCE:
                reviews.append(
                    _validation_row(
                        receipt_id=receipt_id,
                        field_name="line_total",
                        confidence_score=_mismatch_confidence(
                            actual=line_total, expected=expected_total
                        ),
                    )
                )

    # Line items exclude tax, so they must be compared against a tax-exclusive
    # figure: subtotal directly, or total minus consumption_tax when subtotal
    # itself wasn't resolved. Comparing against total alone (tax included)
    # would spuriously flag a mismatch on every ordinary taxed receipt.
    subtotal: float | None = receipt.get("subtotal")
    total: float | None = receipt.get("total")
    expected_line_total: float | None
    if subtotal is not None:
        expected_line_total = subtotal
    elif total is not None:
        expected_line_total = total - (receipt.get("consumption_tax") or 0)
    else:
        expected_line_total = None

    if expected_line_total is not None:
        if not line_items:
            # A receipt with a nonzero expected sum but zero line items is
            # almost certainly an extraction failure (every item was missed,
            # or the receipt has no purchasable items at all), not a
            # legitimately empty receipt -- flag it rather than silently
            # skipping the sum check because there's nothing to sum.
            if abs(expected_line_total) >= _TOLERANCE:
                reviews.append(
                    _validation_row(
                        receipt_id=receipt_id, field_name="missing_line_item"
                    )
                )
        else:
            line_total_sum = sum(
                v for item in line_items if (v := item.get("line_total")) is not None
            )
            if abs(line_total_sum - expected_line_total) >= _TOLERANCE:
                reviews.append(
                    _validation_row(
                        receipt_id=receipt_id,
                        field_name="line_item",
                        confidence_score=_mismatch_confidence(
                            actual=line_total_sum, expected=expected_line_total
                        ),
                    )
                )

    return reviews
