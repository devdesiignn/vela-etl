# Phase 3 — Transform pipeline

## Context

Phase 1 (schema foundation) and Phase 4 (Load: mock vela-api + LoadClient) are done. Phase 2 (Extract interface) is partially done: `ExtractionResult`/`Candidate`/`Confidence` and their sub-models already exist in `src/etl/extract/types.py`, confirmed with the user during that phase (see `docs/work/extraction-phase-plan.md`). The `Extractor` Protocol and orchestrator are not started, but Phase 3 doesn't need them — it consumes `ExtractionResult` lists directly, built by hand as fixtures.

Phase 3 implements `reconcile`, `validate`, `shape`, `emit` per `docs/design/DESIGN-V3.md`'s "Transform pipeline" section and the `extraction_reviews` row rules from that section and from `CLAUDE.md`.

The submodule pin was checked at the start of this session: `vendor/vela-core` is already at `origin/main`'s latest commit (`c693b37`), no update needed.

Two points confirmed with the user this session:

1. **Validator**: pydantic only, not `jsonschema`. The generated types (`src/etl/types/*_schema.py`) are produced directly from the vendored `vela-core` JSON Schema via `datamodel-code-generator`, so constructing a `Receipt`/`LineItem`/`Store`/`ExtractionReview` instance from shaped data already enforces the schema's required fields, types, and constraints — that construction _is_ the re-validation step. Running `jsonschema` against the same vendored files on top would duplicate the same check through a second path with no independent guarantee, since both read from the same source files. Documented as a code comment in `shape.py` (why only one validator is in use, per the design doc's "document why if both end up used" instruction).
2. **`shape()`'s extras split**: introspect `Receipt.model_fields` at runtime (pydantic v2 model introspection). Any resolved receipt-level key matching a real field name on the generated `Receipt` model becomes a column; everything else goes into `Receipt.extras`. `LineItem` and `Store` have no `extras` field in the schema, so their resolved keys must map 1:1 onto real columns (`CandidateLineItem`/`CandidateStore` already only define real schema fields, so this is naturally satisfied — no extras logic needed for those two). Zero-maintenance: a `vela-core` schema change + `gen:types` regen is picked up automatically with no code change here.

## Existing type to build against (`src/etl/extract/types.py`)

```python
CandidateStore    = {name, address, phone?, email?, website?}
CandidateLineItem = {description, quantity, unit_price, line_total}
CandidateReceipt  = {source_image_id, transaction_ref, transaction_ref_label?, register_ref?,
                      date, time?, staff_name?, customer_name?, payment_method?,
                      subtotal?, discount?, vat?, consumption_tax?, total, total_in_words?,
                      extras?, line_items?: list[CandidateLineItem]}
Candidate         = {store: CandidateStore, receipt: CandidateReceipt, line_items: list[CandidateLineItem]}
FieldConfidence   = dict[str, float]                  # field name -> score
Confidence        = {store: FieldConfidence, receipt: FieldConfidence, line_items: list[FieldConfidence]}
ExtractionResult  = {source: str, candidate: Candidate, confidence: Confidence}
```

Note: `Candidate.line_items` is the top-level, authoritative line-items list for reconciliation (positionally aligned with `Confidence.line_items`); `CandidateReceipt.line_items` is a duplicate slot inherited from mirroring `Receipt`'s shape but is not used by Transform — `reconcile()`/`shape()` read line items from `Candidate.line_items` only, and this will be called out in a comment to avoid confusion.

## Files to add

```txt
src/etl/transform/reconcile.py    # reconcile()
src/etl/transform/validate.py     # validate()
src/etl/transform/shape.py        # shape(), content_hash computation, EmitResult's Store/Receipt/LineItem construction
src/etl/transform/emit.py         # emit(), EmitResult dataclass
src/etl/transform/__init__.py     # re-export the four functions (currently empty; matches load/__init__.py's pattern)
tests/test_transform.py
```

## Function designs

### `reconcile(results: list[ExtractionResult], receipt_id: str) -> tuple[dict, list[ExtractionReview]]`

- Per receipt-level field (union of keys across all `results[i].candidate.receipt`): gather each extractor's value for that field. `len(distinct values) == 1` → resolved value (same code path whether 1 or N extractors ran, and whether N agree). Otherwise → one `ExtractionReview` per disagreeing extractor: same `receipt_id` + `field_name`, `flagged_reason="conflicting_extractions"`, `status="pending"`, `confidence_score` from that extractor's `confidence.receipt[field]` (fallback `0.0` if absent — shouldn't happen for a field with a value, but guards a malformed fixture), `extractor_source=results[i].source`, `extracted_value=str(value)`.
- Same per-field logic for `Candidate.store` fields, using `confidence.store[field]`.
- Per line item: align `Candidate.line_items` across extractors positionally (index-by-index) — simplest approach given hand-built fixtures; real cross-extractor line-item matching (by description similarity etc.) is out of scope for Phase 3 and noted as a known simplification. For indices present in all extractors' lists, reconcile per-field the same way, using `confidence.line_items[i][field]`, with `line_item_id` set (a placeholder synthetic id per item, since real ids don't exist until Load — reuse index as `line_item_id` context isn't a real UUID; document that `line_item_id` on review rows for pre-Load items is only meaningful once matched to a persisted line item, and for now Transform emits it as `None` at this stage, relying on `field_name` + row order + receipt_id to disambiguate — revisit if this becomes a real pain point in Phase 6 end-to-end wiring).
  - If one extractor's `line_items` list is longer than another's → the extra trailing indices are `missing_line_item` rows: `field_name="missing_line_item"`, no `line_item_id`, `extracted_value=None`, `extractor_source` = the extractor(s) that has more items than the shortest list, `flagged_reason="conflicting_extractions"` (it's a disagreement about existence, still fits that reason — no separate sentinel reason exists in `FlaggedReason`).
  - If per-field reconciliation within one aligned line item disagrees on 2+ fields simultaneously (a strong signal the whole row is a different item, not a field typo) → emit a single `field_name="line_item"` row instead of multiple per-field rows for that index. Threshold: "more than one field mismatched at this index" (documented inline as a heuristic, not a hard schema rule — this is a design judgment call flagged as such in the code).
- Returns `(resolved_values, review_rows)` where `resolved_values` is a plain dict shaped like `Candidate` (store/receipt/line_items, values only, no confidence) — the "winning" candidate ready for `validate()`.

### `validate(resolved: dict, receipt_id: str) -> list[ExtractionReview]`

Runs only on `reconcile()`'s resolved values. Checks:

- Per line item: `abs(quantity * unit_price - line_total) < 0.01` (float tolerance).
- `sum(line_total for line items) == (subtotal or total)` (same tolerance), using whichever of `subtotal`/`total` is present, preferring `subtotal` if both are.
- Required fields non-null per the generated models' required-field sets (`Receipt`: `total`, `transaction_ref`, `date`, `source_image_id`; `LineItem`: `description`, `quantity`, `unit_price`, `line_total`; `Store`: `name`, `address`).

Each failure → `ExtractionReview(flagged_reason="validation_failed", status="pending", extractor_source="validation", confidence_score=<derived, e.g. 0.0 for a hard arithmetic failure — this is the design doc's origin #3, "derived by us via validation", always available>, field_name=<the specific field or "line_item" for a whole-row sum mismatch>, receipt_id=receipt_id)`.

### `shape(resolved: dict, store_id: str, receipt_id: str) -> tuple[Store, Receipt, list[LineItem]]`

- Store: construct directly (`CandidateStore` fields map 1:1 onto `Store` fields, no extras).
- Receipt: split `resolved["receipt"]` keys into `Receipt.model_fields` matches (columns) vs. everything else (`extras`). Assign `content_hash` via `hashlib.sha256("|".join([str(store_id), transaction_ref, str(date), str(total)]).encode()).hexdigest()` — exact join order/format documented inline as load-bearing (must stay stable across runs since it's the sole dedup key `vela-api` checks).
- Line items: assign `line_order` 1-indexed over `resolved["line_items"]`, in list order. `CandidateLineItem` fields map 1:1 onto `LineItem` fields, no extras split needed there.
- Constructing `Store`/`Receipt`/`LineItem` here is the pydantic re-validation step (see "Two points confirmed" above) — a comment notes why no separate `jsonschema` pass follows.

### `emit(store, receipt, line_items, reconcile_reviews, validate_reviews) -> EmitResult`

- `EmitResult` dataclass (mirrors `load/client.py`'s `Created`/`Duplicate` pattern): `store: Store`, `receipt: Receipt`, `line_items: list[LineItem]`, `extraction_reviews: list[ExtractionReview]` — the concatenation of `reconcile_reviews + validate_reviews`.
- No logic beyond combining lists, matching the design doc's "Emit" section.

## `extraction_reviews` sentinel case coverage

| Rule                                                              | Where implemented                                                         |
| ----------------------------------------------------------------- | ------------------------------------------------------------------------- |
| Single wrong field                                                | `reconcile()` per-field disagreement / `validate()` per-field failure     |
| Whole line item wrong (`field_name="line_item"`)                  | `reconcile()`, multi-field-mismatch heuristic at one aligned index        |
| Missed item (`field_name="missing_line_item"`, no `line_item_id`) | `reconcile()`, trailing-index handling when line-item list lengths differ |
| Multi-extractor conflict, one row per extractor                   | `reconcile()`'s default per-field disagreement path                       |
| Agreement → zero rows                                             | `reconcile()`, `len(distinct values) == 1` short-circuit                  |

## Tests (`tests/test_transform.py`)

Hand-built `ExtractionResult` fixtures built from the real `Candidate`/`Confidence`/`ExtractionResult` models in `src/etl/extract/types.py` — no real extractors, no `vela-api`. Flat module-level constants, matching `tests/test_load_client.py`'s style.

1. Single extractor → `reconcile()` pass-through, zero review rows.
2. Two extractors agreeing on every field → zero review rows.
3. Two extractors disagreeing on one receipt field → exactly one review row per extractor, `flagged_reason="conflicting_extractions"`.
4. Two extractors disagreeing on one line-item field → same, with `line_item_id` handling per the design above.
5. `validate()`: `quantity * unit_price != line_total` → one row, `flagged_reason="validation_failed"`.
6. `validate()`: line items don't sum to total → one row, `flagged_reason="validation_failed"`.
7. Missed line item (shorter list from one extractor) → `field_name="missing_line_item"` row, `line_item_id is None`.
8. Whole line item wrong (2+ fields mismatched at one aligned index) → single `field_name="line_item"` row, not multiple per-field rows.
9. `shape()`: an unrecognized `CandidateReceipt`-adjacent key lands in `Receipt.extras`, not as a top-level attribute; `content_hash` is deterministic across two calls with identical inputs.
10. `emit()`: `EmitResult.extraction_reviews` is the concatenation of reconcile + validate reviews in order; `store`/`receipt`/`line_items` round-trip through the real generated pydantic models without validation errors.

## Verification

- `uv run poe test` — new tests pass; `test_types.py`/`test_load_client.py` unaffected.
- `uv run poe lint` — clean.
- Manual sanity: build one two-extractor-agreeing fixture and one disagreeing fixture in a scratch script, run `reconcile → validate → shape → emit`, confirm the resulting `EmitResult.store`/`.receipt`/`.extraction_reviews` are accepted directly by `LoadClient.ingest()`'s existing signature (`Store`, `Receipt`, `extraction_reviews: list[ExtractionReview] | None`) — closes the loop with Phase 4 without needing `vela-api`.
