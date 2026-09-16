# Phase 2 — Extractor interface & orchestrator

## Context

Phase 1 (schema foundation) and Phase 4 (Load: mock vela-api + LoadClient) are done. Phase 2 is next per `docs/IMPLEMENTATION-PLAN.md`: define the `ExtractionResult` shape, the `extract(image) -> ExtractionResult` Protocol, and a config-driven orchestrator that runs N extractors and always returns a uniform results list — never branching on which extractor ran or how many. This unblocks Phase 3 (Transform, which consumes `ExtractionResult` lists) while deferring real OCR/vision-LLM adapters to Phase 5/7.

The submodule pin was checked at the start of this work: `vendor/vela-core` was already at the latest commit on `origin/main` (`c693b37`), no update needed.

Two design decisions not fully spelled out in DESIGN-V3.md were confirmed with the user:

1. **Candidate typing**: the generated `Store`/`Receipt`/`LineItem` types (`src/etl/types/*_schema.py`) require non-null `id` (and `store_id`/`receipt_id`) UUID fields — server-assigned, not available pre-Load. So `ExtractionResult.candidate` cannot reuse the generated types directly. New partial pydantic models (`CandidateStore`, `CandidateReceipt`, `CandidateLineItem`) carry the same fields minus server-assigned ids (and minus `content_hash`/`line_order`, which Transform's `shape()` computes later).
2. **Confidence shape**: nested, mirroring the candidate structure — `{store: {field: score}, receipt: {field: score}, line_items: [{field: score}, ...]}`, with `line_items` positional (same length/order as `candidate.line_items`). This lets Transform's `reconcile()` walk extractor results in parallel without parsing dotted-string paths, and maps directly to `(field_name, line_item index)` when building `extraction_reviews` rows.

## Implementation

### 1. `src/etl/extract/types.py` — ✅ done

- `CandidateStore(BaseModel)`: same fields as `Store` minus `id`.
- `CandidateReceipt(BaseModel)`: same fields as `Receipt` minus `id`, `store_id`, `content_hash`; `line_items: list[CandidateLineItem] | None`.
- `CandidateLineItem(BaseModel)`: same fields as `LineItem` minus `id`, `receipt_id`, `line_order` (Transform's `shape()` assigns `line_order`).
- `Candidate(BaseModel)`: `{store: CandidateStore, receipt: CandidateReceipt, line_items: list[CandidateLineItem]}`.
- `FieldConfidence = dict[str, float]` (field name → score, per section).
- `Confidence(BaseModel)`: `{store: FieldConfidence, receipt: FieldConfidence, line_items: list[FieldConfidence]}`.
- `ExtractionResult(BaseModel)`: `{source: str, candidate: Candidate, confidence: Confidence}`.
- Exported from `src/etl/extract/__init__.py`.
- Verified: `uv run poe lint` passes; manual construct/validate round-trip confirmed via a REPL smoke test (valid `ExtractionResult` builds and serializes correctly).

### 2. `src/etl/extract/protocol.py` — not started

- `Extractor(Protocol)`: `def extract(self, image: ...) -> ExtractionResult | ExtractionReview: ...`
  - Return type is a union: normal path returns `ExtractionResult`; total-failure path (unreadable image, API error) returns an `ExtractionReview`-shaped row directly (per DESIGN-V3.md line 87), using the existing `etl.types.extraction_review_schema.ExtractionReview`, with `flagged_reason="illegible"` as the documented example.
  - `image` type: keep loose (`bytes` or a small `Image` alias) since no real adapter exists yet in this phase — confirm exact type only when Phase 5 adapters land.

### 3. `src/etl/extract/orchestrator.py` — not started

- `def run_extractors(extractors: list[Extractor], image) -> list[ExtractionResult | ExtractionReview]`
  - Iterates the config-driven list, calls `.extract(image)` on each, appends each return value (whichever variant) to one results list.
  - No branching on count (1 vs N) or on which extractor ran — same loop always, per the design doc's stated invariant.
  - Config-driven extractor list itself: a plain `list[Extractor]` passed in by the caller (full config wiring is Phase 6's job — this phase just needs the orchestrator to accept a list and stay agnostic to its length/contents).

### 4. `src/etl/extract/__init__.py` — partially done

Types are exported (step 1). Still need to add `Extractor` and `run_extractors` once steps 2–3 land, mirroring the `__init__.py` re-export pattern already used in `src/etl/load/__init__.py`.

### 5. Tests — `tests/test_orchestrator.py` — not started

Stub extractors only, no real OCR/vision adapters (matches Phase 5 deferral):

- A stub extractor returning a normal `ExtractionResult`.
- A second stub extractor (used in a 2-extractor config) to confirm the results list has length 2 with the same shape as length 1 — proves no special-casing.
- A stub extractor returning an `ExtractionReview` (total-failure path) to confirm the orchestrator passes it through unchanged, mixed into the same results list alongside `ExtractionResult` entries from other extractors.
- Confirm `run_extractors([], image)` returns `[]` (zero extractors — still no branch).

## Verification

- `uv run poe lint` and `uv run poe format` — style is `pydantic` + generated-type conventions already in the repo.
- `uv run poe test` (runs `pretest` → `bootstrap` first, so it's self-sufficient).
- Manual sanity: construct one `ExtractionResult` by hand in a REPL/script, confirm pydantic validation accepts a well-formed candidate and rejects a malformed one (missing required `receipt.total`, etc.) — mirrors the round-trip test pattern already used in `tests/test_types.py`. Done for step 1; repeat for the Protocol/orchestrator once built.
