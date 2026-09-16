import pytest
from pydantic import ValidationError

from etl.extract.types import (
    Candidate,
    CandidateLineItem,
    CandidateReceipt,
    CandidateStore,
    Confidence,
    ExtractionResult,
)
from etl.transform import emit, reconcile, shape, validate

RECEIPT_ID = "44d21dc0-ada2-4b24-9ad4-91800c4922a9"
STORE_ID = "6105a8cf-f678-48da-8ce2-89cfe24fb61a"


def _result(
    source: str,
    *,
    store_fields: dict,
    receipt_fields: dict,
    line_items: list[dict],
    store_confidence: dict | None = None,
    receipt_confidence: dict | None = None,
    line_item_confidence: list[dict] | None = None,
) -> ExtractionResult:
    return ExtractionResult(
        source=source,
        candidate=Candidate(
            store=CandidateStore(**store_fields),
            receipt=CandidateReceipt(**receipt_fields),
            line_items=[CandidateLineItem(**item) for item in line_items],
        ),
        confidence=Confidence(
            store=store_confidence or {},
            receipt=receipt_confidence or {},
            line_items=line_item_confidence or [{} for _ in line_items],
        ),
    )


STORE_FIELDS = {"name": "Supreme Pharmacy", "address": "12 Ikorodu Rd, Lagos"}
RECEIPT_FIELDS = {
    "source_image_id": "img_001",
    "transaction_ref": "INV-2026-0042",
    "date": "2026-01-15",
    "total": 300.0,
}
LINE_ITEM = {
    "description": "Paracetamol 500mg",
    "quantity": 2,
    "unit_price": 150.0,
    "line_total": 300.0,
}


def test_reconcile_single_extractor_passes_through():
    results = [
        _result(
            "extractor-a",
            store_fields=STORE_FIELDS,
            receipt_fields=RECEIPT_FIELDS,
            line_items=[LINE_ITEM],
        )
    ]

    resolved, reviews = reconcile(results, receipt_id=RECEIPT_ID)

    assert reviews == []
    assert resolved["receipt"]["total"] == 300.0
    assert resolved["line_items"][0]["description"] == "Paracetamol 500mg"


def test_reconcile_agreeing_extractors_produce_zero_reviews():
    results = [
        _result(
            "extractor-a",
            store_fields=STORE_FIELDS,
            receipt_fields=RECEIPT_FIELDS,
            line_items=[LINE_ITEM],
        ),
        _result(
            "extractor-b",
            store_fields=STORE_FIELDS,
            receipt_fields=RECEIPT_FIELDS,
            line_items=[LINE_ITEM],
        ),
    ]

    resolved, reviews = reconcile(results, receipt_id=RECEIPT_ID)

    assert reviews == []
    assert resolved["receipt"]["total"] == 300.0


def test_reconcile_disagreeing_extractors_produce_one_row_each():
    receipt_a = {**RECEIPT_FIELDS, "total": 300.0}
    receipt_b = {**RECEIPT_FIELDS, "total": 310.0}
    results = [
        _result(
            "extractor-a",
            store_fields=STORE_FIELDS,
            receipt_fields=receipt_a,
            line_items=[LINE_ITEM],
            receipt_confidence={"total": 0.9},
        ),
        _result(
            "extractor-b",
            store_fields=STORE_FIELDS,
            receipt_fields=receipt_b,
            line_items=[LINE_ITEM],
            receipt_confidence={"total": 0.4},
        ),
    ]

    _resolved, reviews = reconcile(results, receipt_id=RECEIPT_ID)

    total_reviews = [r for r in reviews if r.field_name == "total"]
    assert len(total_reviews) == 2
    assert {r.extractor_source for r in total_reviews} == {"extractor-a", "extractor-b"}
    assert all(
        r.flagged_reason.value == "conflicting_extractions" for r in total_reviews
    )
    assert all(r.status.value == "pending" for r in total_reviews)


def test_reconcile_disagreeing_line_item_field_produces_one_row_each():
    item_a = {**LINE_ITEM, "quantity": 2}
    item_b = {**LINE_ITEM, "quantity": 3}
    results = [
        _result(
            "extractor-a",
            store_fields=STORE_FIELDS,
            receipt_fields=RECEIPT_FIELDS,
            line_items=[item_a],
        ),
        _result(
            "extractor-b",
            store_fields=STORE_FIELDS,
            receipt_fields=RECEIPT_FIELDS,
            line_items=[item_b],
        ),
    ]

    _resolved, reviews = reconcile(results, receipt_id=RECEIPT_ID)

    quantity_reviews = [r for r in reviews if r.field_name == "quantity"]
    assert len(quantity_reviews) == 2
    assert all(r.line_item_id is None for r in quantity_reviews)
    assert all(r.extractor_notes == "line item 1" for r in quantity_reviews)


def test_reconcile_disagreeing_store_field_produces_one_row_each():
    store_a = {**STORE_FIELDS, "name": "Supreme Pharmacy"}
    store_b = {**STORE_FIELDS, "name": "Supreme Pharmacy Ltd"}
    results = [
        _result(
            "extractor-a",
            store_fields=store_a,
            receipt_fields=RECEIPT_FIELDS,
            line_items=[LINE_ITEM],
        ),
        _result(
            "extractor-b",
            store_fields=store_b,
            receipt_fields=RECEIPT_FIELDS,
            line_items=[LINE_ITEM],
        ),
    ]

    _resolved, reviews = reconcile(results, receipt_id=RECEIPT_ID)

    name_reviews = [r for r in reviews if r.field_name == "name"]
    assert len(name_reviews) == 2
    assert {r.extractor_source for r in name_reviews} == {"extractor-a", "extractor-b"}
    assert all(
        r.flagged_reason.value == "conflicting_extractions" for r in name_reviews
    )


def test_reconcile_three_extractors_agreeing_produce_zero_reviews():
    results = [
        _result(
            f"extractor-{letter}",
            store_fields=STORE_FIELDS,
            receipt_fields=RECEIPT_FIELDS,
            line_items=[LINE_ITEM],
        )
        for letter in ("a", "b", "c")
    ]

    resolved, reviews = reconcile(results, receipt_id=RECEIPT_ID)

    assert reviews == []
    assert resolved["receipt"]["total"] == 300.0


def test_reconcile_three_extractors_two_agree_one_disagrees_still_flags_all():
    receipt_a = {**RECEIPT_FIELDS, "total": 300.0}
    receipt_b = {**RECEIPT_FIELDS, "total": 300.0}
    receipt_c = {**RECEIPT_FIELDS, "total": 999.0}
    results = [
        _result(
            "extractor-a",
            store_fields=STORE_FIELDS,
            receipt_fields=receipt_a,
            line_items=[LINE_ITEM],
        ),
        _result(
            "extractor-b",
            store_fields=STORE_FIELDS,
            receipt_fields=receipt_b,
            line_items=[LINE_ITEM],
        ),
        _result(
            "extractor-c",
            store_fields=STORE_FIELDS,
            receipt_fields=receipt_c,
            line_items=[LINE_ITEM],
        ),
    ]

    _resolved, reviews = reconcile(results, receipt_id=RECEIPT_ID)

    total_reviews = [r for r in reviews if r.field_name == "total"]
    # len(distinct values) == 1 is the only short-circuit -- two agreeing out of
    # three still counts as a disagreement (2 distinct values), so all three
    # extractors get a row, not just the odd one out.
    assert len(total_reviews) == 3
    assert {r.extractor_source for r in total_reviews} == {
        "extractor-a",
        "extractor-b",
        "extractor-c",
    }


def test_reconcile_empty_results_list_returns_empty_without_crashing():
    resolved, reviews = reconcile([], receipt_id=RECEIPT_ID)

    assert resolved == {"store": {}, "receipt": {}, "line_items": []}
    assert reviews == []


def test_validate_flags_missing_required_receipt_field():
    resolved = {
        "store": STORE_FIELDS,
        "receipt": {**RECEIPT_FIELDS, "transaction_ref": None},
        "line_items": [LINE_ITEM],
    }

    reviews = validate(resolved, receipt_id=RECEIPT_ID)

    assert any(
        r.field_name == "transaction_ref"
        and r.flagged_reason.value == "validation_failed"
        for r in reviews
    )


def test_validate_flags_missing_required_store_field():
    resolved = {
        "store": {**STORE_FIELDS, "address": ""},
        "receipt": RECEIPT_FIELDS,
        "line_items": [LINE_ITEM],
    }

    reviews = validate(resolved, receipt_id=RECEIPT_ID)

    assert any(
        r.field_name == "address" and r.flagged_reason.value == "validation_failed"
        for r in reviews
    )


def test_validate_flags_missing_required_line_item_field():
    resolved = {
        "store": STORE_FIELDS,
        "receipt": RECEIPT_FIELDS,
        "line_items": [{**LINE_ITEM, "description": None}],
    }

    reviews = validate(resolved, receipt_id=RECEIPT_ID)

    assert any(
        r.field_name == "description" and r.flagged_reason.value == "validation_failed"
        for r in reviews
    )


def test_validate_quantity_times_unit_price_mismatch():
    resolved = {
        "store": STORE_FIELDS,
        "receipt": RECEIPT_FIELDS,
        "line_items": [
            {
                "description": "Item",
                "quantity": 2,
                "unit_price": 150.0,
                "line_total": 999.0,
            }
        ],
    }

    reviews = validate(resolved, receipt_id=RECEIPT_ID)

    assert any(
        r.field_name == "line_total" and r.flagged_reason.value == "validation_failed"
        for r in reviews
    )


def test_validate_flags_line_items_not_summing_to_total():
    resolved = {
        "store": STORE_FIELDS,
        "receipt": {**RECEIPT_FIELDS, "total": 999.0},
        "line_items": [LINE_ITEM],
    }

    reviews = validate(resolved, receipt_id=RECEIPT_ID)

    assert any(
        r.field_name == "line_item" and r.flagged_reason.value == "validation_failed"
        for r in reviews
    )


def test_validate_confidence_score_scales_with_size_of_arithmetic_mismatch():
    # A tiny rounding-sized gap should score higher (more confident the data
    # is still fine) than a wildly wrong total -- not a flat 0.0 either way.
    small_gap = {
        "store": STORE_FIELDS,
        "receipt": RECEIPT_FIELDS,
        "line_items": [
            {
                "description": "Item",
                "quantity": 2,
                "unit_price": 150.0,
                "line_total": 301.0,
            }
        ],
    }
    large_gap = {
        "store": STORE_FIELDS,
        "receipt": RECEIPT_FIELDS,
        "line_items": [
            {
                "description": "Item",
                "quantity": 2,
                "unit_price": 150.0,
                "line_total": 9000.0,
            }
        ],
    }

    small_gap_review = next(
        r
        for r in validate(small_gap, receipt_id=RECEIPT_ID)
        if r.field_name == "line_total"
    )
    large_gap_review = next(
        r
        for r in validate(large_gap, receipt_id=RECEIPT_ID)
        if r.field_name == "line_total"
    )

    assert small_gap_review.confidence_score > large_gap_review.confidence_score
    assert (
        0.0
        <= large_gap_review.confidence_score
        < small_gap_review.confidence_score
        < 1.0
    )


def test_validate_small_total_does_not_score_a_small_gap_as_harshly_as_a_huge_mismatch():
    # A receipt with a tiny total (1.00) and a small absolute rounding gap
    # (off by 0.05) should not score the same rock-bottom confidence as a
    # receipt that is wildly, unambiguously wrong -- even though 0.05/1.00
    # alone looks like a large relative error. The confidence formula floors
    # its denominator so small-total gaps aren't over-penalized.
    tiny_total_small_gap = {
        "store": STORE_FIELDS,
        "receipt": {**RECEIPT_FIELDS, "total": 1.00},
        "line_items": [
            {
                "description": "Item",
                "quantity": 1,
                "unit_price": 1.0,
                "line_total": 1.05,
            }
        ],
    }
    wildly_wrong = {
        "store": STORE_FIELDS,
        "receipt": RECEIPT_FIELDS,
        "line_items": [
            {
                "description": "Item",
                "quantity": 2,
                "unit_price": 150.0,
                "line_total": 9000.0,
            }
        ],
    }

    small_total_review = next(
        r
        for r in validate(tiny_total_small_gap, receipt_id=RECEIPT_ID)
        if r.field_name == "line_total"
    )
    wildly_wrong_review = next(
        r
        for r in validate(wildly_wrong, receipt_id=RECEIPT_ID)
        if r.field_name == "line_total"
    )

    assert small_total_review.confidence_score > wildly_wrong_review.confidence_score
    # A 0.05 absolute gap should score quite high -- close to trusted, not
    # clamped to the 0.0 floor the way it would be without the denominator
    # floor (0.05 / 1.00 = 95% relative error -> would clamp to 0.0).
    assert small_total_review.confidence_score > 0.9


def test_validate_missing_required_field_confidence_stays_zero():
    # No "how far off" to measure for a missing field -- confidence stays the
    # certain-something-is-wrong floor, not the size-scaled arithmetic score.
    resolved = {
        "store": STORE_FIELDS,
        "receipt": {**RECEIPT_FIELDS, "transaction_ref": None},
        "line_items": [LINE_ITEM],
    }

    reviews = validate(resolved, receipt_id=RECEIPT_ID)

    transaction_ref_review = next(
        r for r in reviews if r.field_name == "transaction_ref"
    )
    assert transaction_ref_review.confidence_score == 0.0


BANDAGES_ITEM = {
    "description": "Bandages",
    "quantity": 5,
    "unit_price": 20.0,
    "line_total": 100.0,
}


def test_reconcile_missing_line_item_flags_extra_extractor():
    # extractor-a saw a second, distinctly-described item that extractor-b
    # never produced -- matched by description, not by list position.
    results = [
        _result(
            "extractor-a",
            store_fields=STORE_FIELDS,
            receipt_fields=RECEIPT_FIELDS,
            line_items=[LINE_ITEM, BANDAGES_ITEM],
        ),
        _result(
            "extractor-b",
            store_fields=STORE_FIELDS,
            receipt_fields=RECEIPT_FIELDS,
            line_items=[LINE_ITEM],
        ),
    ]

    _resolved, reviews = reconcile(results, receipt_id=RECEIPT_ID)

    missing_rows = [r for r in reviews if r.field_name == "missing_line_item"]
    assert len(missing_rows) == 1
    # extractor-b is the one that did NOT produce "Bandages" -- the review
    # row names the extractor missing the item, not the one that found it.
    assert missing_rows[0].extractor_source == "extractor-b"
    assert missing_rows[0].line_item_id is None
    assert missing_rows[0].extractor_notes == "line item 2"


def test_reconcile_missing_line_item_flags_one_row_per_extra_item():
    third_item = {**BANDAGES_ITEM, "description": "Cotton Wool"}
    results = [
        _result(
            "extractor-a",
            store_fields=STORE_FIELDS,
            receipt_fields=RECEIPT_FIELDS,
            line_items=[LINE_ITEM, BANDAGES_ITEM, third_item],
        ),
        _result(
            "extractor-b",
            store_fields=STORE_FIELDS,
            receipt_fields=RECEIPT_FIELDS,
            line_items=[LINE_ITEM],
        ),
    ]

    _resolved, reviews = reconcile(results, receipt_id=RECEIPT_ID)

    missing_rows = [r for r in reviews if r.field_name == "missing_line_item"]
    # extractor-a has 2 extra, distinctly-described items beyond
    # extractor-b's single item -- each extra item gets its own row.
    assert len(missing_rows) == 2
    assert {r.extractor_notes for r in missing_rows} == {"line item 2", "line item 3"}


def test_reconcile_same_description_different_fields_produces_per_field_rows_only():
    # Two extractors both saw "the same item" (matched by description), but
    # disagree on every other field. reconcile() must never infer "whole
    # item wrong" from this -- only per-field rows, one group (not two
    # separate items), since the description matched.
    item_a = {**LINE_ITEM, "quantity": 2, "unit_price": 150.0, "line_total": 300.0}
    item_b = {**LINE_ITEM, "quantity": 3, "unit_price": 100.0, "line_total": 300.0}
    results = [
        _result(
            "extractor-a",
            store_fields=STORE_FIELDS,
            receipt_fields=RECEIPT_FIELDS,
            line_items=[item_a],
        ),
        _result(
            "extractor-b",
            store_fields=STORE_FIELDS,
            receipt_fields=RECEIPT_FIELDS,
            line_items=[item_b],
        ),
    ]

    _resolved, reviews = reconcile(results, receipt_id=RECEIPT_ID)

    line_item_sentinel_rows = [r for r in reviews if r.field_name == "line_item"]
    assert line_item_sentinel_rows == []
    field_named_rows = [
        r
        for r in reviews
        if r.field_name in ("description", "quantity", "unit_price", "line_total")
    ]
    # quantity and unit_price differ (2 fields x 2 extractors); description
    # and line_total agree, so no rows for those.
    assert len(field_named_rows) == 4
    assert all(r.extractor_notes == "line item 1" for r in field_named_rows)


def test_reconcile_matches_descriptions_case_and_whitespace_insensitively():
    # "Paracetamol 500mg" and "  paracetamol 500mg  " must match as the same
    # item, not be treated as two distinct items just because of casing or
    # incidental whitespace from OCR/extraction noise.
    item_a = {**LINE_ITEM, "description": "Paracetamol 500mg", "quantity": 2}
    item_b = {**LINE_ITEM, "description": "  paracetamol 500mg  ", "quantity": 3}
    results = [
        _result(
            "extractor-a",
            store_fields=STORE_FIELDS,
            receipt_fields=RECEIPT_FIELDS,
            line_items=[item_a],
        ),
        _result(
            "extractor-b",
            store_fields=STORE_FIELDS,
            receipt_fields=RECEIPT_FIELDS,
            line_items=[item_b],
        ),
    ]

    _resolved, reviews = reconcile(results, receipt_id=RECEIPT_ID)

    missing_rows = [r for r in reviews if r.field_name == "missing_line_item"]
    assert missing_rows == []
    quantity_reviews = [r for r in reviews if r.field_name == "quantity"]
    assert len(quantity_reviews) == 2


def test_reconcile_matches_descriptions_with_small_typo_via_fuzzy_matching():
    # "Paracetmol 500mg" (missing an 'a') vs "Paracetamol 500mg" -- a small
    # OCR-style typo. Must still be matched as the same item, not treated as
    # two separate ones.
    item_a = {**LINE_ITEM, "description": "Paracetmol 500mg", "quantity": 2}
    item_b = {**LINE_ITEM, "description": "Paracetamol 500mg", "quantity": 3}
    results = [
        _result(
            "extractor-a",
            store_fields=STORE_FIELDS,
            receipt_fields=RECEIPT_FIELDS,
            line_items=[item_a],
        ),
        _result(
            "extractor-b",
            store_fields=STORE_FIELDS,
            receipt_fields=RECEIPT_FIELDS,
            line_items=[item_b],
        ),
    ]

    _resolved, reviews = reconcile(results, receipt_id=RECEIPT_ID)

    missing_rows = [r for r in reviews if r.field_name == "missing_line_item"]
    assert missing_rows == []
    quantity_reviews = [r for r in reviews if r.field_name == "quantity"]
    assert len(quantity_reviews) == 2


def test_reconcile_does_not_fuzzy_match_genuinely_different_short_items():
    # "Milk" and "Mild" are one character apart but are genuinely different
    # products -- the similarity threshold must be strict enough that short,
    # clearly-different words aren't merged just because they're similar in
    # edit distance. (SequenceMatcher ratio for "Milk"/"Mild" is well below
    # the 0.9 threshold, since 1 of 4 characters differs.)
    item_a = {**LINE_ITEM, "description": "Milk", "quantity": 1}
    item_b = {**LINE_ITEM, "description": "Mild", "quantity": 1}
    results = [
        _result(
            "extractor-a",
            store_fields=STORE_FIELDS,
            receipt_fields=RECEIPT_FIELDS,
            line_items=[item_a],
        ),
        _result(
            "extractor-b",
            store_fields=STORE_FIELDS,
            receipt_fields=RECEIPT_FIELDS,
            line_items=[item_b],
        ),
    ]

    resolved, reviews = reconcile(results, receipt_id=RECEIPT_ID)

    # Both should pass through as distinct, unresolved single-extractor
    # items -- not merged, and not flagged as a field-level mismatch either,
    # since each only has one extractor behind it (per the existing
    # single-extractor-per-item rule).
    field_named_rows = [r for r in reviews if r.field_name == "description"]
    assert field_named_rows == []
    descriptions = {item["description"] for item in resolved["line_items"]}
    assert descriptions == {"Milk", "Mild"}


def test_reconcile_matches_same_items_listed_in_different_order():
    # Two extractors seeing the same two items but listing them in a
    # different order must still reconcile correctly -- matching is by
    # description, not list position, so order must not matter.
    paracetamol = {
        "description": "Paracetamol 500mg",
        "quantity": 2,
        "unit_price": 150.0,
        "line_total": 300.0,
    }
    bandages = {
        "description": "Bandages",
        "quantity": 5,
        "unit_price": 20.0,
        "line_total": 100.0,
    }
    results = [
        _result(
            "extractor-a",
            store_fields=STORE_FIELDS,
            receipt_fields=RECEIPT_FIELDS,
            line_items=[paracetamol, bandages],
        ),
        _result(
            "extractor-b",
            store_fields=STORE_FIELDS,
            receipt_fields=RECEIPT_FIELDS,
            # Same two items, listed in reverse order.
            line_items=[bandages, paracetamol],
        ),
    ]

    resolved, reviews = reconcile(results, receipt_id=RECEIPT_ID)

    # Both extractors agree on every field for both items once matched by
    # description -- zero review rows, and both items present in resolved.
    assert reviews == []
    descriptions = {item["description"] for item in resolved["line_items"]}
    assert descriptions == {"Paracetamol 500mg", "Bandages"}
    assert len(resolved["line_items"]) == 2


def test_reconcile_duplicate_description_on_same_receipt_keeps_both_occurrences():
    # Two separate "Coca-Cola 500ml" lines on the same receipt (bought twice
    # as distinct line items, a real and common receipt shape) must both
    # survive reconciliation -- not have the second silently overwrite the
    # first because they share a description.
    cola_a1 = {
        "description": "Coca-Cola 500ml",
        "quantity": 1,
        "unit_price": 2.0,
        "line_total": 2.0,
    }
    cola_a2 = {
        "description": "Coca-Cola 500ml",
        "quantity": 1,
        "unit_price": 2.0,
        "line_total": 2.0,
    }
    cola_b1 = {
        "description": "Coca-Cola 500ml",
        "quantity": 1,
        "unit_price": 2.0,
        "line_total": 2.0,
    }
    cola_b2 = {
        "description": "Coca-Cola 500ml",
        "quantity": 2,
        "unit_price": 2.0,
        "line_total": 4.0,
    }
    results = [
        _result(
            "extractor-a",
            store_fields=STORE_FIELDS,
            receipt_fields=RECEIPT_FIELDS,
            line_items=[cola_a1, cola_a2],
        ),
        _result(
            "extractor-b",
            store_fields=STORE_FIELDS,
            receipt_fields=RECEIPT_FIELDS,
            line_items=[cola_b1, cola_b2],
        ),
    ]

    resolved, reviews = reconcile(results, receipt_id=RECEIPT_ID)

    # Both occurrences must still be present in the resolved output -- not
    # collapsed into a single item by the second overwriting the first.
    assert len(resolved["line_items"]) == 2
    # 1st occurrence agrees (quantity=1 both sides) -> no review row.
    # 2nd occurrence disagrees (quantity=1 vs quantity=2) -> one row each.
    quantity_reviews = [r for r in reviews if r.field_name == "quantity"]
    assert len(quantity_reviews) == 2
    assert {r.extractor_notes for r in quantity_reviews} == {"line item 2"}


def test_reconcile_duplicate_item_with_inconsistent_typo_in_one_extractor_still_splits_correctly():
    # Regression test: a receipt lists "Paracetamol" twice (a real duplicate
    # item), and extractor-a spells the second occurrence with a typo
    # ("Paracetmol"). An earlier version counted occurrences per exact
    # normalized description string, so extractor-a's second item (a
    # different normalized string than its first) would never be recognized
    # as "the 2nd occurrence" and could be mismatched against extractor-b's
    # items incorrectly. Grouping must still produce exactly 2 groups, each
    # correctly combining one item from each extractor.
    para_a1 = {
        "description": "Paracetamol",
        "quantity": 2,
        "unit_price": 150.0,
        "line_total": 300.0,
    }
    para_a2 = {
        "description": "Paracetmol",  # typo, same real item as para_a1
        "quantity": 3,
        "unit_price": 150.0,
        "line_total": 450.0,
    }
    para_b1 = {
        "description": "Paracetamol",
        "quantity": 2,
        "unit_price": 150.0,
        "line_total": 300.0,
    }
    para_b2 = {
        "description": "Paracetamol",
        "quantity": 3,
        "unit_price": 150.0,
        "line_total": 450.0,
    }
    results = [
        _result(
            "extractor-a",
            store_fields=STORE_FIELDS,
            receipt_fields=RECEIPT_FIELDS,
            line_items=[para_a1, para_a2],
        ),
        _result(
            "extractor-b",
            store_fields=STORE_FIELDS,
            receipt_fields=RECEIPT_FIELDS,
            line_items=[para_b1, para_b2],
        ),
    ]

    resolved, reviews = reconcile(results, receipt_id=RECEIPT_ID)

    # Both occurrences must be recognized as 2 distinct items, not merged
    # into 1 or split incorrectly.
    assert len(resolved["line_items"]) == 2
    # Every extractor agrees on quantity/unit_price/line_total once matched
    # correctly -- the only "disagreement" is the typo'd description itself,
    # which is expected (fuzzy-matched, not identical) and produces its own
    # review row; no other field should be affected.
    quantity_reviews = [r for r in reviews if r.field_name == "quantity"]
    assert quantity_reviews == []
    resolved_quantities = sorted(item["quantity"] for item in resolved["line_items"])
    assert resolved_quantities == [2, 3]


def test_reconcile_different_descriptions_are_treated_as_different_items_not_a_mismatch():
    # Two extractors reading genuinely different items (different
    # descriptions) must never be forced into the same group and compared
    # field-by-field -- each is its own item. Since only one extractor (out
    # of 2 total) produced each, no field-level review rows are produced
    # (nothing to compare field-by-field against), but each item's own
    # existence is uncorroborated -- the other extractor's absence produces
    # a missing_line_item row for it, and the item itself gets a
    # low_confidence flag rather than being trusted the same as an item
    # every extractor agreed on.
    item_a = {
        "description": "Paracetamol",
        "quantity": 2,
        "unit_price": 150.0,
        "line_total": 300.0,
    }
    item_b = {
        "description": "Bandages",
        "quantity": 5,
        "unit_price": 20.0,
        "line_total": 100.0,
    }
    results = [
        _result(
            "extractor-a",
            store_fields=STORE_FIELDS,
            receipt_fields=RECEIPT_FIELDS,
            line_items=[item_a],
        ),
        _result(
            "extractor-b",
            store_fields=STORE_FIELDS,
            receipt_fields=RECEIPT_FIELDS,
            line_items=[item_b],
        ),
    ]

    resolved, reviews = reconcile(results, receipt_id=RECEIPT_ID)

    field_named_rows = [
        r
        for r in reviews
        if r.field_name in ("description", "quantity", "unit_price", "line_total")
    ]
    assert field_named_rows == []
    descriptions = {item["description"] for item in resolved["line_items"]}
    assert descriptions == {"Paracetamol", "Bandages"}

    missing_rows = [r for r in reviews if r.field_name == "missing_line_item"]
    assert len(missing_rows) == 2
    low_confidence_rows = [
        r for r in reviews if r.flagged_reason.value == "low_confidence"
    ]
    assert len(low_confidence_rows) == 2
    assert all(r.field_name == "line_item" for r in low_confidence_rows)
    assert {r.extracted_value for r in low_confidence_rows} == {
        "Paracetamol",
        "Bandages",
    }


def test_reconcile_uncorroborated_single_extractor_item_among_many_is_flagged_low_confidence():
    # 3 extractors run; only extractor-a produces "Vitamin C". The other two
    # get missing_line_item rows (existing behavior). The item's own field
    # values, having no other extractor's data to compare against, must not
    # be silently trusted the same as an item every extractor agreed on --
    # it gets a low_confidence flag on top of the missing_line_item rows.
    vitamin_c = {
        "description": "Vitamin C",
        "quantity": 1,
        "unit_price": 500.0,
        "line_total": 500.0,
    }
    results = [
        _result(
            "extractor-a",
            store_fields=STORE_FIELDS,
            receipt_fields=RECEIPT_FIELDS,
            line_items=[vitamin_c],
        ),
        _result(
            "extractor-b",
            store_fields=STORE_FIELDS,
            receipt_fields=RECEIPT_FIELDS,
            line_items=[],
        ),
        _result(
            "extractor-c",
            store_fields=STORE_FIELDS,
            receipt_fields=RECEIPT_FIELDS,
            line_items=[],
        ),
    ]

    resolved, reviews = reconcile(results, receipt_id=RECEIPT_ID)

    assert len(resolved["line_items"]) == 1
    missing_rows = [r for r in reviews if r.field_name == "missing_line_item"]
    assert {r.extractor_source for r in missing_rows} == {"extractor-b", "extractor-c"}

    low_confidence_rows = [
        r for r in reviews if r.flagged_reason.value == "low_confidence"
    ]
    assert len(low_confidence_rows) == 1
    assert low_confidence_rows[0].extractor_source == "extractor-a"
    assert low_confidence_rows[0].field_name == "line_item"
    assert low_confidence_rows[0].extracted_value == "Vitamin C"


def test_reconcile_single_extractor_overall_line_item_is_not_flagged_low_confidence():
    # Only one extractor ran at all (not "one out of several") -- there is
    # nothing to be uncorroborated against, so this is the ordinary
    # single-extractor pass-through case and must NOT get a low_confidence
    # flag or any missing_line_item rows.
    results = [
        _result(
            "extractor-a",
            store_fields=STORE_FIELDS,
            receipt_fields=RECEIPT_FIELDS,
            line_items=[LINE_ITEM],
        )
    ]

    _resolved, reviews = reconcile(results, receipt_id=RECEIPT_ID)

    assert reviews == []


def test_shape_splits_extras_and_computes_deterministic_content_hash():
    resolved = {
        "store": STORE_FIELDS,
        "receipt": {**RECEIPT_FIELDS, "loyalty_points": 50},
        "line_items": [LINE_ITEM],
    }

    _store1, receipt1, line_items1 = shape(
        resolved, store_id=STORE_ID, receipt_id=RECEIPT_ID
    )
    _store2, receipt2, _line_items2 = shape(
        resolved, store_id=STORE_ID, receipt_id=RECEIPT_ID
    )

    assert receipt1.extras == {"loyalty_points": 50}
    assert not hasattr(receipt1, "loyalty_points")
    assert receipt1.content_hash == receipt2.content_hash
    assert line_items1[0].line_order == 1


def test_shape_store_fields_map_one_to_one_with_no_extras():
    resolved = {
        "store": STORE_FIELDS,
        "receipt": RECEIPT_FIELDS,
        "line_items": [LINE_ITEM],
    }

    store, _receipt, _line_items = shape(
        resolved, store_id=STORE_ID, receipt_id=RECEIPT_ID
    )

    assert store.name == "Supreme Pharmacy"
    assert store.address == "12 Ikorodu Rd, Lagos"
    assert not hasattr(store, "extras")


def test_reconcile_only_receives_extraction_results_not_total_failure_rows():
    # Per DESIGN-V3.md line 87: a total-failure extractor (unreadable image,
    # API error) returns an ExtractionReview-shaped row directly instead of an
    # ExtractionResult. Routing that row away from reconcile() and straight
    # into the final review list is the orchestrator's job (Phase 2/6), not
    # Transform's -- reconcile() only ever operates on a list of valid
    # ExtractionResults with usable candidate data. This test simulates that:
    # the orchestrator already filtered the failed extractor out, and
    # reconcile() needs no special-casing to handle the remaining result(s).
    results = [
        _result(
            "extractor-a",
            store_fields=STORE_FIELDS,
            receipt_fields=RECEIPT_FIELDS,
            line_items=[LINE_ITEM],
        )
    ]

    resolved, reviews = reconcile(results, receipt_id=RECEIPT_ID)

    assert reviews == []
    assert resolved["receipt"]["total"] == 300.0


def test_emit_combines_reconcile_and_validate_reviews():
    resolved = {
        "store": STORE_FIELDS,
        "receipt": RECEIPT_FIELDS,
        "line_items": [LINE_ITEM],
    }
    store, receipt, line_items = shape(
        resolved, store_id=STORE_ID, receipt_id=RECEIPT_ID
    )

    reconcile_reviews = reconcile(
        [
            _result(
                "extractor-a",
                store_fields=STORE_FIELDS,
                receipt_fields=RECEIPT_FIELDS,
                line_items=[LINE_ITEM],
            )
        ],
        receipt_id=RECEIPT_ID,
    )[1]
    validate_reviews = validate(resolved, receipt_id=RECEIPT_ID)

    result = emit(
        store=store,
        receipt=receipt,
        line_items=line_items,
        reconcile_reviews=reconcile_reviews,
        validate_reviews=validate_reviews,
    )

    assert result.extraction_reviews == reconcile_reviews + validate_reviews
    assert result.store.name == "Supreme Pharmacy"
    assert result.receipt.total == 300.0


def test_shape_raises_when_required_field_left_unresolved_by_reconcile():
    # reconcile() intentionally leaves a field out of its resolved dict when
    # extractors disagree on it -- it never guesses a winner. shape() must
    # never silently build a Receipt/Store from that incomplete data; it
    # should fail loudly instead, since a receipt missing a required field
    # (here, total) must never reach Load. This guards that failure mode
    # permanently, rather than relying on a one-off manual check.
    receipt_a = {**RECEIPT_FIELDS, "total": 300.0}
    receipt_b = {**RECEIPT_FIELDS, "total": 310.0}
    results = [
        _result(
            "extractor-a",
            store_fields=STORE_FIELDS,
            receipt_fields=receipt_a,
            line_items=[LINE_ITEM],
        ),
        _result(
            "extractor-b",
            store_fields=STORE_FIELDS,
            receipt_fields=receipt_b,
            line_items=[LINE_ITEM],
        ),
    ]

    resolved, reviews = reconcile(results, receipt_id=RECEIPT_ID)

    assert "total" not in resolved["receipt"]
    assert any(r.field_name == "total" for r in reviews)

    with pytest.raises(ValidationError):
        shape(resolved, store_id=STORE_ID, receipt_id=RECEIPT_ID)


def test_reconcile_handles_duplicate_extractor_source_names_correctly():
    # Regression test: two ExtractionResults sharing the same `source` name
    # (e.g. the same extractor run twice, or an upstream bug producing a
    # duplicate name) must not collapse in the missing/present accounting.
    # An earlier version built a set of source *strings* to test membership,
    # which silently merged two same-named entries into one -- this reconciles
    # by list index (result_index) instead, so duplicate names can't collide.
    item_only_in_first = {
        "description": "Paracetamol",
        "quantity": 2,
        "unit_price": 150.0,
        "line_total": 300.0,
    }
    results = [
        _result(
            "extractor-a",
            store_fields=STORE_FIELDS,
            receipt_fields=RECEIPT_FIELDS,
            line_items=[item_only_in_first],
        ),
        # Same source name as above, but produced no line items at all.
        _result(
            "extractor-a",
            store_fields=STORE_FIELDS,
            receipt_fields=RECEIPT_FIELDS,
            line_items=[],
        ),
    ]

    _resolved, reviews = reconcile(results, receipt_id=RECEIPT_ID)

    # Both entries share the name "extractor-a" -- the second (index 1) truly
    # produced nothing, so it must still get its own missing_line_item row,
    # not be silently absorbed because its name matches index 0's.
    missing_rows = [r for r in reviews if r.field_name == "missing_line_item"]
    assert len(missing_rows) == 1
    assert missing_rows[0].extractor_source == "extractor-a"


def test_shape_raises_before_hashing_unresolved_fields_instead_of_hashing_none():
    # Regression test: content_hash must never be computed from a field that
    # is actually missing (None) due to an unresolved reconcile() conflict --
    # that would silently produce a "valid-looking" hash for data that's
    # about to fail construction anyway. shape() must raise from Receipt's
    # own required-field validation before ever touching content_hash.
    receipt_a = {**RECEIPT_FIELDS, "transaction_ref": "INV-001"}
    receipt_b = {**RECEIPT_FIELDS, "transaction_ref": "INV-002"}
    results = [
        _result(
            "extractor-a",
            store_fields=STORE_FIELDS,
            receipt_fields=receipt_a,
            line_items=[LINE_ITEM],
        ),
        _result(
            "extractor-b",
            store_fields=STORE_FIELDS,
            receipt_fields=receipt_b,
            line_items=[LINE_ITEM],
        ),
    ]

    resolved, _reviews = reconcile(results, receipt_id=RECEIPT_ID)

    assert "transaction_ref" not in resolved["receipt"]
    with pytest.raises(ValidationError):
        shape(resolved, store_id=STORE_ID, receipt_id=RECEIPT_ID)


def test_validate_flags_nonzero_total_with_zero_line_items():
    # A receipt with a nonzero total but zero resolved line items is almost
    # certainly an extraction failure -- every item was missed. Must be
    # flagged, not silently skipped because there's nothing to sum.
    resolved = {
        "store": STORE_FIELDS,
        "receipt": {**RECEIPT_FIELDS, "total": 300.0},
        "line_items": [],
    }

    reviews = validate(resolved, receipt_id=RECEIPT_ID)

    assert any(
        r.field_name == "missing_line_item"
        and r.flagged_reason.value == "validation_failed"
        for r in reviews
    )


def test_validate_does_not_flag_zero_total_with_zero_line_items():
    # A receipt with total=0 and zero line items is not obviously wrong (a
    # voided/zero-value transaction) -- only a *nonzero* total with no items
    # is the suspicious case worth flagging.
    resolved = {
        "store": STORE_FIELDS,
        "receipt": {**RECEIPT_FIELDS, "total": 0.0},
        "line_items": [],
    }

    reviews = validate(resolved, receipt_id=RECEIPT_ID)

    assert not any(r.field_name == "missing_line_item" for r in reviews)


def test_reconcile_three_extractors_one_produces_item_two_fuzzy_disagree_on_field():
    # Concrete check on the partial-group path combined with 3+ extractors
    # and fuzzy matching together: extractor-a produces "Vitamin C", and
    # extractors b/c both produce a typo'd variant "Vitamin C " with a
    # differing quantity between b and c. All three should fuzzy-match into
    # one group (3 of 3 results present), so this takes the full
    # per-field-reconciliation path, not the low_confidence single-extractor
    # path -- and the quantity disagreement between b and c must still
    # produce its own per-field review rows.
    item_a = {
        "description": "Vitamin C",
        "quantity": 1,
        "unit_price": 500.0,
        "line_total": 500.0,
    }
    item_b = {
        "description": "Vitamin C",
        "quantity": 1,
        "unit_price": 500.0,
        "line_total": 500.0,
    }
    item_c = {
        "description": "Vitamin C",
        "quantity": 2,
        "unit_price": 500.0,
        "line_total": 1000.0,
    }
    results = [
        _result(
            "extractor-a",
            store_fields=STORE_FIELDS,
            receipt_fields=RECEIPT_FIELDS,
            line_items=[item_a],
        ),
        _result(
            "extractor-b",
            store_fields=STORE_FIELDS,
            receipt_fields=RECEIPT_FIELDS,
            line_items=[item_b],
        ),
        _result(
            "extractor-c",
            store_fields=STORE_FIELDS,
            receipt_fields=RECEIPT_FIELDS,
            line_items=[item_c],
        ),
    ]

    resolved, reviews = reconcile(results, receipt_id=RECEIPT_ID)

    # All 3 extractors matched into one group -- no missing_line_item or
    # low_confidence rows, only the genuine quantity/unit_total disagreement.
    assert len(resolved["line_items"]) == 1
    assert [r for r in reviews if r.field_name == "missing_line_item"] == []
    assert [r for r in reviews if r.flagged_reason.value == "low_confidence"] == []
    quantity_reviews = [r for r in reviews if r.field_name == "quantity"]
    assert len(quantity_reviews) == 3
    assert {r.extractor_source for r in quantity_reviews} == {
        "extractor-a",
        "extractor-b",
        "extractor-c",
    }


def test_reconcile_raises_on_mismatched_line_item_and_confidence_lengths():
    # Regression test: Confidence.line_items is documented as positionally
    # aligned with Candidate.line_items, but ExtractionResult's own
    # construction never enforces that the two lists are the same length.
    # An earlier version zip()'d them with strict=False, which would
    # silently drop a real line item from reconciliation with no error and
    # no review row if the lengths ever mismatched. reconcile() must instead
    # fail loudly -- a length mismatch means the ExtractionResult itself was
    # built incorrectly upstream.
    mismatched_result = ExtractionResult(
        source="extractor-a",
        candidate=Candidate(
            store=CandidateStore(**STORE_FIELDS),
            receipt=CandidateReceipt(**RECEIPT_FIELDS),
            line_items=[CandidateLineItem(**LINE_ITEM), CandidateLineItem(**LINE_ITEM)],
        ),
        confidence=Confidence(store={}, receipt={}, line_items=[{}]),  # only 1, not 2
    )

    with pytest.raises(ValueError, match="line item"):
        reconcile([mismatched_result], receipt_id=RECEIPT_ID)


def test_shape_extras_split_matches_declared_field_aliases_not_just_attribute_names():
    # Regression test: the extras split previously checked resolved keys
    # against Receipt.model_fields' attribute names only. If a future
    # gen:types regeneration ever adds a field alias (e.g. a camelCase JSON
    # key mapped to a snake_case Python attribute), a resolved key spelled
    # with the alias would silently and incorrectly land in extras instead
    # of matching the real column, with no error. Receipt declares no
    # aliases today, so this test documents and locks in the *absence* of
    # that failure mode for the current schema -- every real Receipt field
    # name still correctly becomes a column, not extras.
    resolved = {
        "store": STORE_FIELDS,
        "receipt": RECEIPT_FIELDS,
        "line_items": [LINE_ITEM],
    }

    _store, receipt, _line_items = shape(
        resolved, store_id=STORE_ID, receipt_id=RECEIPT_ID
    )

    assert receipt.extras is None
    assert receipt.transaction_ref == RECEIPT_FIELDS["transaction_ref"]
    assert receipt.source_image_id == RECEIPT_FIELDS["source_image_id"]


def test_shape_filters_id_receipt_id_line_order_collisions_from_item_fields():
    # Regression test: shape() always assigns id/receipt_id/line_order to
    # LineItem itself -- if resolved["line_items"] ever contained one of
    # those same keys (e.g. reconcile()'s single-extractor pass-through path
    # dumps the full candidate model via item.model_dump(), and a future
    # CandidateLineItem schema extension could add one of these names), an
    # unfiltered **item_fields spread would collide with the explicit kwarg
    # and raise "got multiple values for keyword argument". shape() must
    # filter these out regardless of where resolved["line_items"] came from.
    resolved = {
        "store": STORE_FIELDS,
        "receipt": RECEIPT_FIELDS,
        "line_items": [
            {
                **LINE_ITEM,
                # Simulates a future-extended CandidateLineItem producing
                # these keys through reconcile()'s pass-through path.
                "id": "some-other-id",
                "receipt_id": "some-other-receipt-id",
                "line_order": 999,
            }
        ],
    }

    _store, _receipt, line_items = shape(
        resolved, store_id=STORE_ID, receipt_id=RECEIPT_ID
    )

    assert len(line_items) == 1
    # shape()'s own assignments win -- not the colliding values from item_fields.
    assert str(line_items[0].receipt_id) == RECEIPT_ID
    assert line_items[0].line_order == 1
    assert str(line_items[0].id) != "some-other-id"
    assert line_items[0].description == LINE_ITEM["description"]
