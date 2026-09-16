"""Reconciliation: per field, per receipt, compare all extractor candidate values.

Per DESIGN-V3.md's "Transform pipeline" -> "1. Reconcile":
len(distinct values) == 1 -> use the value directly (covers both "only one
extractor ran" and "multiple extractors agreed" -- same code path). Otherwise
-> one review row per disagreeing extractor, flagged_reason="conflicting_extractions".

reconcile() never knows or asks how many extractors ran -- it only looks at
the values in the list it was handed.
"""

from __future__ import annotations

from difflib import SequenceMatcher
from uuid import uuid4

from etl.extract.types import ExtractionResult
from etl.types.extraction_review_schema import ExtractionReview

# Candidate.line_items is the authoritative line-items list for reconciliation.
# Items are matched across extractors by normalized description, not list
# position -- extractors don't reliably return items in the same order (or
# with the same count), so position-only matching would misreconcile real
# data. CandidateReceipt.line_items is an unused duplicate slot inherited
# from mirroring Receipt's shape.


def _distinct(values: list) -> list:
    seen = []
    for value in values:
        if value not in seen:
            seen.append(value)
    return seen


def _conflict_rows(
    *,
    receipt_id: str,
    field_name: str,
    line_item_id: str | None,
    per_extractor_values: list[tuple[str, object, float]],
    extractor_notes: str | None = None,
) -> list[ExtractionReview]:
    return [
        ExtractionReview(
            id=str(uuid4()),
            receipt_id=receipt_id,
            line_item_id=line_item_id,
            field_name=field_name,
            extractor_source=source,
            extracted_value=None if value is None else str(value),
            confidence_score=confidence,
            flagged_reason="conflicting_extractions",
            status="pending",
            extractor_notes=extractor_notes,
        )
        for source, value, confidence in per_extractor_values
    ]


def _reconcile_fields(
    *,
    sources: list[str],
    candidates: list,
    confidences: list[dict],
    field_names: set[str],
    receipt_id: str,
    line_item_id: str | None = None,
    extractor_notes: str | None = None,
) -> tuple[dict, list[ExtractionReview]]:
    """Resolve each field name across aligned (source, candidate, confidence) triples.

    A field resolves when every candidate agrees; otherwise one conflict row
    per candidate is emitted instead. Shared by section-level (store/receipt)
    and line-item reconciliation -- both are "per field, compare all
    extractor candidates" over a different set of (candidate, confidence)
    pairs.
    """
    resolved: dict = {}
    reviews: list[ExtractionReview] = []

    for field_name in field_names:
        per_extractor = [
            (source, getattr(candidate, field_name), confidence.get(field_name, 0.0))
            for source, candidate, confidence in zip(
                sources, candidates, confidences, strict=True
            )
        ]
        values = [value for _, value, _ in per_extractor]
        distinct = _distinct(values)

        if len(distinct) == 1:
            resolved[field_name] = distinct[0]
        else:
            reviews.extend(
                _conflict_rows(
                    receipt_id=receipt_id,
                    field_name=field_name,
                    line_item_id=line_item_id,
                    per_extractor_values=per_extractor,
                    extractor_notes=extractor_notes,
                )
            )

    return resolved, reviews


def _reconcile_section(
    results: list[ExtractionResult],
    *,
    section: str,
    receipt_id: str,
) -> tuple[dict, list[ExtractionReview]]:
    """Reconcile one flat candidate section (store or receipt) across extractors."""
    candidates = [getattr(result.candidate, section) for result in results]
    confidences = [getattr(result.confidence, section) for result in results]

    field_names: set[str] = set()
    for candidate in candidates:
        field_names.update(candidate.model_fields_set)

    return _reconcile_fields(
        sources=[result.source for result in results],
        candidates=candidates,
        confidences=confidences,
        field_names=field_names,
        receipt_id=receipt_id,
    )


def _normalize_description(description: str) -> str:
    return description.strip().casefold()


# Similarity threshold for treating two different descriptions as the same
# item (e.g. "Paracetmol 500mg" vs "Paracetamol 500mg", a 1-character OCR
# typo). Conservative on purpose: high enough that genuinely different
# products (different scores well below this) are never merged together,
# while still catching small single-character-class typos. difflib's
# SequenceMatcher.ratio() is exact-match at 1.0, so 0.9 tolerates only a
# small fraction of a short description differing.
_DESCRIPTION_SIMILARITY_THRESHOLD = 0.9


def _description_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


class _Group:
    __slots__ = ("description_key", "matches")

    def __init__(self, description_key: str) -> None:
        self.description_key = description_key
        self.matches: dict[int, tuple] = {}


def _group_line_items_by_description(
    results: list[ExtractionResult],
) -> list[dict[int, tuple]]:
    """Match line items across extractors by description, not list position.

    Each item is assigned to a group one at a time: first, an existing group
    with the exact same normalized description that this extractor hasn't
    already filled; failing that, the most similar existing group (see
    _DESCRIPTION_SIMILARITY_THRESHOLD) this extractor hasn't already filled,
    to tolerate small OCR-style typos between extractors; failing that, a
    brand new group. Because matching is driven by "is there a compatible,
    still-open group" rather than a precomputed per-description occurrence
    count, a receipt listing the same product twice (e.g. two separate
    "Coca-Cola 500ml" lines) correctly produces two groups even if one
    extractor spells the two occurrences inconsistently (e.g. one has a
    typo) -- the second occurrence finds the first group already filled by
    this extractor and opens a second, rather than being miscounted by
    normalized string alone.

    Returns one group per matched item-cluster, in first-seen order. Each
    group maps extractor-index -> (item, confidence) for every extractor
    that produced a matching item. An extractor missing from a group simply
    didn't produce a matching item.
    """
    groups: list[_Group] = []

    for result_index, result in enumerate(results):
        items = result.candidate.line_items
        confidences = result.confidence.line_items
        if len(items) != len(confidences):
            # Confidence.line_items is documented (see extract/types.py) as
            # positionally aligned with Candidate.line_items -- an invariant
            # ExtractionResult's own construction does not enforce. Silently
            # zipping the shorter length here would drop a real line item
            # from reconciliation with no error and no review row. Fail
            # loudly instead: a mismatch means the ExtractionResult itself
            # was built incorrectly (an extractor/orchestrator bug), which
            # reconcile() should never paper over.
            raise ValueError(
                f"ExtractionResult from {result.source!r} has "
                f"{len(items)} line item(s) but {len(confidences)} "
                "confidence entries -- Candidate.line_items and "
                "Confidence.line_items must be the same length."
            )
        for item, confidence in zip(items, confidences, strict=True):
            description_key = _normalize_description(item.description)
            group = _find_open_group(description_key, groups, result_index)
            if group is None:
                group = _Group(description_key)
                groups.append(group)
            group.matches[result_index] = (item, confidence)

    return [group.matches for group in groups]


def _find_open_group(
    description_key: str,
    groups: list[_Group],
    result_index: int,
) -> _Group | None:
    """Find a group for this description that `result_index` hasn't already
    filled: an exact normalized-description match first, else the most
    similar match above _DESCRIPTION_SIMILARITY_THRESHOLD.
    """
    open_groups = [g for g in groups if result_index not in g.matches]

    for group in open_groups:
        if group.description_key == description_key:
            return group

    best_group: _Group | None = None
    best_score = _DESCRIPTION_SIMILARITY_THRESHOLD
    for group in open_groups:
        score = _description_similarity(description_key, group.description_key)
        if score > best_score:
            best_score = score
            best_group = group

    return best_group


def _reconcile_line_items(
    results: list[ExtractionResult],
    *,
    receipt_id: str,
) -> tuple[list[dict], list[ExtractionReview]]:
    if not results:
        return [], []

    sources = [result.source for result in results]
    groups = _group_line_items_by_description(results)

    resolved_items: list[dict] = []
    reviews: list[ExtractionReview] = []

    for position, group in enumerate(groups, start=1):
        # Position note: real line_item ids don't exist until shape() runs
        # after reconcile(), so a review row can't carry line_item_id yet.
        # Recording the 1-indexed position here (matching shape()'s later
        # line_order) lets a reviewer tell which item on the receipt a
        # per-field disagreement or a missing item refers to.
        position_note = f"line item {position}"

        if len(group) < len(results):
            # Description matched in some extractors' lists but not all --
            # the extractors present in the group (by list index, not by
            # source name -- two ExtractionResults can share the same
            # extractor `source` string) agree the item exists; the indices
            # absent from it didn't produce it at all.
            present_indices = set(group)
            for i, source in enumerate(sources):
                if i not in present_indices:
                    reviews.append(
                        ExtractionReview(
                            id=str(uuid4()),
                            receipt_id=receipt_id,
                            line_item_id=None,
                            field_name="missing_line_item",
                            extractor_source=source,
                            extracted_value=None,
                            confidence_score=0.0,
                            flagged_reason="conflicting_extractions",
                            status="pending",
                            extractor_notes=position_note,
                        )
                    )
            if len(present_indices) < 2:
                # Only one extractor produced this item -- nothing to
                # reconcile per-field against, so its values pass through
                # unresolved-per-field. But when other extractors ran and
                # simply didn't produce this item (missing_line_item rows
                # were just added above for them), the item's own existence
                # is disputed, not just uncorroborated on individual fields.
                # Flag it low_confidence so it isn't treated the same as an
                # item every present extractor agreed on. This does not
                # apply when there was only ever one extractor total --
                # that's the ordinary, legitimate single-extractor
                # pass-through case, with nothing to be disputed against.
                # present_indices always has exactly one member here: a
                # group is only ever created when the first item is added to
                # it (see _group_line_items_by_description), so len(group)
                # is never 0, and this branch is reached only when it's < 2.
                only_index = next(iter(present_indices))
                item, confidence = group[only_index]
                source = sources[only_index]
                if len(results) > 1:
                    reviews.append(
                        ExtractionReview(
                            id=str(uuid4()),
                            receipt_id=receipt_id,
                            line_item_id=None,
                            field_name="line_item",
                            extractor_source=source,
                            extracted_value=item.description,
                            confidence_score=confidence.get("description", 0.0),
                            flagged_reason="low_confidence",
                            status="pending",
                            extractor_notes=position_note,
                        )
                    )
                resolved_items.append(item.model_dump())
                continue

        aligned = list(group.values())
        aligned_sources = [sources[i] for i in group]
        aligned_items = [item for item, _confidence in aligned]
        aligned_confidences = [confidence for _item, confidence in aligned]

        field_names: set[str] = set()
        for item in aligned_items:
            field_names.update(item.model_fields_set)

        item_resolved, item_reviews = _reconcile_fields(
            sources=aligned_sources,
            candidates=aligned_items,
            confidences=aligned_confidences,
            field_names=field_names,
            receipt_id=receipt_id,
            extractor_notes=position_note,
        )
        reviews.extend(item_reviews)
        resolved_items.append(item_resolved)

    return resolved_items, reviews


def reconcile(
    results: list[ExtractionResult],
    *,
    receipt_id: str,
) -> tuple[dict, list[ExtractionReview]]:
    """Reconcile a list of ExtractionResults into one resolved candidate + review rows."""
    store_resolved, store_reviews = _reconcile_section(
        results, section="store", receipt_id=receipt_id
    )
    receipt_resolved, receipt_reviews = _reconcile_section(
        results, section="receipt", receipt_id=receipt_id
    )
    line_items_resolved, line_item_reviews = _reconcile_line_items(
        results, receipt_id=receipt_id
    )

    resolved = {
        "store": store_resolved,
        "receipt": receipt_resolved,
        "line_items": line_items_resolved,
    }
    reviews = store_reviews + receipt_reviews + line_item_reviews
    return resolved, reviews
