# Implementation Plan

Build roadmap derived from [`design/DESIGN-V3.md`](./design/DESIGN-V3.md). This plan follows dependency order. Earlier phases unblock later ones. Phases marked "parallel" have no dependency on each other and can be done in either order.

**Dependency order and effort order are not the same axis.** They point in opposite directions here. Of the three ETL stages, Load is the least non-trivial. It is one HTTP call against a combined-payload endpoint, with no branching logic of its own. Transform is the middle. It has four pure functions, but real business-logic rules to get exactly right: reconciliation, validation, the extraction_reviews sentinel cases. Extract is the most tedious by a wide margin. It needs N vendor adapters, each with its own response shape to map. It also needs image preprocessing and OCR-specific parsing by hand. It needs retry and backoff, plus three different confidence origins to reconcile.

This is why the team builds Load _early_ (Phase 4) against a mock, in parallel with Extract's skeleton. It is low-effort and worth clearing out of the way immediately, not because dependency order requires it early. Extract's real weight, the adapters themselves, is deliberately pushed later, to Phases 5 and 7. That is where the actual implementation cost sits. The phase numbers below reflect what unblocks what. They do not reflect effort. Do not read "Phase 4" as somehow harder than "Phase 7."

---

## Tooling decisions

| Decision                    | Choice                                                      | Why                                                                                                                                               |
| --------------------------- | ----------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| Dependency manager          | [uv](https://docs.astral.sh/uv/)                            | One tool for venv, install, and lockfile. Fastest onramp for a from-scratch Python project.                                                       |
| `vela-core` schema sourcing | Git submodule, pinned to a commit                           | Conventional git-native mechanism for a pinned, trackable snapshot of another repo's content. Git enforces the version relationship, not memory.  |
| Mock `vela-api`             | Both `respx` (unit tests) and a standalone FastAPI mock app | `respx` gates automated testing. The standalone app lets the full pipeline be run manually end-to-end before the real `vela-api` exists.          |
| CI                          | GitHub Actions running `pytest`                             | Basic regression safety net from the start                                                                                                        |
| Local enforcement           | `pre-commit` + `ruff`                                       | Matches `vela-core`'s existing convention: Husky and lint-staged running eslint and prettier before every commit. Same idea, Python-native tools. |

---

## Named commands

`vela-core` exposes every setup and build operation as a named `npm run <script>` in `package.json`. The developer never has to remember a bare shell command. `uv` has no built-in equivalent (no `[tool.uv.scripts]`), so `vela-etl` matches the pattern via [`poethepoet`](https://github.com/nat-n/poethepoet), a task runner configured entirely in `pyproject.toml`'s `[tool.poe.tasks]` and invoked as `uv run poe <task>`:

| Task               | Command                                                                                                                                                               | Purpose                                                                                                 |
| ------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| `submodule:init`   | `git submodule update --init --recursive`                                                                                                                             | First-time setup after cloning — populates `vendor/vela-core` at its pinned commit                      |
| `submodule:update` | `git -C vendor/vela-core fetch && git -C vendor/vela-core checkout <new-commit> && git add vendor/vela-core`                                                          | Deliberately bump the pinned schema version. Stages the new submodule pointer for commit in `vela-etl`. |
| `gen:types`        | `datamodel-codegen --input vendor/vela-core/schemas --input-file-type jsonschema --output src/etl/types --output-model-type pydantic_v2.BaseModel --formatters black` | Regenerate Python types from the vendored JSON Schema. Run after `submodule:update`, or on first setup. |
| `bootstrap`        | `uv run poe submodule:init && uv run poe gen:types`                                                                                                                   | Single command from a cold clone to a working local setup — mirrors `vela-core`'s `bootstrap:dev`       |
| `test`             | `pytest`                                                                                                                                                              | Run the test suite                                                                                      |
| `lint`             | `ruff check .`                                                                                                                                                        | Lint                                                                                                    |
| `format`           | `ruff format .`                                                                                                                                                       | Format                                                                                                  |
| `mock-api`         | `uvicorn mock_api.app:app --port 2222 --reload`                                                                                                                       | Run the `vela-api` mock locally. Port `2222`, distinct from `vela-core`'s `1111`.                       |

`pretest` runs `bootstrap` the same way `vela-core`'s `pretest` runs `bootstrap:dev`. This makes `uv run poe test` self-sufficient from a cold clone, with no manual setup steps to remember or document separately.

---

## Project scaffolding

```txt
vela-etl/
├── src/
│   └── etl/
│       ├── types/         # generated from vela-core's JSON Schema
│       ├── extract/       # extractor interface + adapters
│       ├── transform/     # reconcile, validate, shape, emit
│       ├── load/          # httpx client for vela-api
│       └── config.py
├── mock_api/              # standalone FastAPI app implementing the sketched OpenAPI spec
├── vendor/vela-core/   # git submodule, pinned commit
├── tests/
├── .pre-commit-config.yaml
├── .github/workflows/ci.yml
└── pyproject.toml         # uv-managed, includes [tool.poe.tasks]
```

This repo follows the standard Python "src layout" convention — it stops tests from importing an uninstalled local copy of the package. The importable package name is `etl`. This repo builds exactly one pipeline, so a longer prefix adds nothing.

---

## Phase 1 — Repo & schema foundation — ✅ done

- [x] `uv init`, set up `pyproject.toml`, lockfile. Named commands added to `pyproject.toml`'s `[tool.poe.tasks]` (see "Named commands" above): `submodule:init`, `submodule:update`, `gen:types`, `bootstrap`, `test`, `lint`, `format`.
- [x] Added `vela-core` as a git submodule at `vendor/vela-core`, pinned to its commit at add-time. First-time setup is `uv run poe submodule:init`. Deliberately bumping the pinned schema version later is `uv run poe submodule:update`.
- [x] `uv run poe gen:types` runs `datamodel-codegen` against `vendor/vela-core/schemas/*.schema.json`, output into `src/etl/types/`. Confirmed schema path matches `vela-core`'s `docs/SCHEMA.md`.
- [x] Round-trip test (`tests/test_generated_types.py`): generated types construct a valid instance and reject an invalid one (missing required field) — covers `Store` and `Receipt` (with nested `LineItem`).
- [x] `pre-commit` set up with `ruff-check --fix` + `ruff-format`, matching `vela-core`'s lint-staged convention. Hook installed and verified to actually rewrite bad code.
- [x] `.github/workflows/ci.yml` set up: runs `uv run poe lint` and `uv run poe test` on push/PR, with submodule checkout.

**Blocks:** everything else. Extract, Transform, and Load all use the generated types.

**Not yet started:** Phase 6 (end-to-end wiring), Phase 7 (remaining adapters & hardening).

---

## Phase 2 — Extractor interface & orchestrator — ✅ done

- [x] `ExtractionResult`, `Candidate`/`CandidateStore`/`CandidateReceipt`/`CandidateLineItem`, and `Confidence` types defined with `pydantic` in `src/etl/extract/types.py`, per the design doc's Extract/Transform tool inventory.
- [x] Extractor `Protocol` (`extract(image) -> ExtractionResult | ExtractionReview`) defined in `src/etl/extract/protocol.py`, plus an `Image = bytes` alias (loose on purpose — no real adapter exists yet).
- [x] Orchestrator (`src/etl/extract/orchestrator.py`): `run_extractors` takes a config-driven list of extractors, runs each, returns the results list unchanged in shape regardless of count (1 or N).
- [x] Total-failure path: an extractor can return an `extraction_reviews`-shaped row directly instead of an `ExtractionResult`. `split_results` partitions the orchestrator's mixed output into `(list[ExtractionResult], list[ExtractionReview])`. `reconcile()` (Phase 3) only accepts `ExtractionResult`s — This confirmed the total-failure sentinel (`field_name="receipt"` + `flagged_reason="extraction_failed"`) needed a `vela-core` schema addition. Commit `1619173` resolved it upstream. This repo then bumped the submodule pin and regenerated types. See `docs/DECISIONS.md`'s "Total-failure `ExtractionReview` shape" entry.
- [x] Tested against hand-written stub extractors only (`tests/test_extract.py`, 9 tests) — no real OCR/vision-LLM adapters yet. This isolates orchestration logic from adapter correctness.

**Not yet handled (correctly out of scope for this phase):** consuming Load's `IngestionResult` (`Created`/`Duplicate`/`ValidationError`), or its unexpected-HTTP-error path. The orchestrator only runs extractors. It sits upstream of Transform and Load. Phase 6 covers that, and it has no caller for `LoadClient.ingest()` yet.

---

## Phase 3 — Transform pipeline — ✅ done

- [x] Implemented `reconcile`, `validate`, `shape`, `emit` as pure functions per the design doc's rules (sentinels, `flagged_reason` values, `line_order`), in `src/etl/transform/`.
- [x] `shape` computes `content_hash` via stdlib `hashlib` as a pure function of `store_id + transaction_ref + date + total`.
- [x] Validator choice: `pydantic` only (see `shape.py`'s module docstring for why `jsonschema` would be redundant — both would confirm the same vendored `.schema.json` files again. Constructing the generated `Store`/`Receipt`/`LineItem` models serves as that second pass).
- [x] Tests (`tests/test_transform.py`) with hand-built `ExtractionResult` fixtures — no real extractors, no `vela-api`, needed to exercise this stage.
- [x] Covers: single extractor (pass-through), agreeing extractors (zero review rows), disagreeing extractors (one review row per extractor), validation failures, missing/extra line items, `extras` catch-all routing.

---

## Phase 4 — Mock vela-api & Load client — ✅ done

- [x] Sketched a minimal OpenAPI spec (`docs/api/openapi.yaml`) for the combined-payload write endpoint. One endpoint (`POST /ingestions`) always requires `store` + `receipt` (nested `line_items`). `extraction_reviews` stays optional on the same request. Per `vela-core`'s `docs/SCHEMA.md`, the extractor interface has two sides, and the payload must carry both. Successfully extracted data goes into `stores` / `receipts` / `line_items` together. `store` is not a separate concern from `receipt`. Both belong in the same request, since the pipeline starts from a bare photo with no pre-existing store row to reference. Anything uncertain or wrong (low confidence, disagreement, a missed item) goes into `extraction_reviews` instead, for the same underlying extracted content. Confirmed `DESIGN-V3.md`'s Load section text omits `store` from its description. The spec corrects it. A follow-up to correct the design doc's own wording is still open.
- [x] `vela-etl` sends the extracted store data with each receipt, unconditionally. It has no database connection, per this repo's own architecture. `LoadClient` strips server-assigned `id`/`store_id`/`receipt_id` fields from the outgoing payload — `vela-api` mints those, not `vela-etl`.
- [x] Built the standalone FastAPI mock app (`mock_api/app.py`) implementing that spec — success (`201`) path, duplicate (`409`, in-memory `content_hash` tracking) path, validation-error (`422`) path. Run locally via `uv run poe mock-api` (port `2222`, distinct from `vela-core`'s Postgres on `1111`).
- [x] Built the `httpx`-based Load client (`src/etl/load/client.py`) against the spec — `LoadClient.ingest(store, receipt, extraction_reviews=None)` returns one of three typed results (`Created`/`Duplicate`/`ValidationError`), context-manager support for connection cleanup.
- [x] Unit tests (`tests/test_load.py`) via `respx`, covering success, payload shape (ids stripped), reviews-included, duplicate, and validation-error paths.
- [x] Manual smoke test: ran the mock app locally via `uvicorn`. Then pointed `LoadClient` at it and confirmed a real HTTP round trip on the created and duplicate paths.

---

## Phase 5 — First real adapters _(depends on Phase 2)_ — ✅ done

- [x] `OpenCV`-based image preprocessing (`src/etl/extract/preprocess.py`): deskew via minimum-area-rect angle correction, then CLAHE contrast + denoise + adaptive threshold cleanup, ahead of the OCR adapter. Isolated and tested on its own (`tests/test_preprocess.py`), independent of any adapter.
- [x] RapidOCR adapter (`src/etl/extract/adapters/rapidocr_adapter.py`) — most adapter work. Raw OCR text has no receipt structure and no native field confidence. RapidOCR returns scattered text boxes, so `_group_boxes_into_lines()` reconstructs physical printed lines from box geometry first. A hand-written regex/heuristic line parser (`ocr_text_parser.py`) then maps that text to candidate fields. Confidence comes from DESIGN-V3.md's third origin: derived, not native. Most fields score "found at all". Line items score on `quantity * unit_price == line_total` arithmetic agreement. The adapter runs OCR on both the raw and the `preprocess()`'d image and keeps whichever parse recovers more fields. It also retries 90/180/270 degrees when a photo looks sideways, detected by OCR grouping into few, unusually long lines.
- [x] RapidOCR adapter hardened against 28 real receipt photos. Real data found bugs no mocked test reached. Corrected: a wrong regex capture group, ignored EXIF orientation, and per-box (not per-line) OCR output. Also date-format gaps, a row-grouping span that swallowed whole receipts, and subtotal-as-total receipts. Also an item-count prefix on totals lines, items printed across two physical lines, and Naira signs misread as `N` or `~`. Also payment lines parsed as purchased items, and a store name picked positionally instead of by score. Six gates now route a partial read to `ExtractionReview` rather than shipping it. Those are: no total and no line items, no date, and a missing total with items present. Also items absent with a total present, a total parsed as literal `0.00`, and line items that do not sum to the total. Current state: 12 of 28 photos extract cleanly, 16 route to review. Reproduce with `uv run python scripts/sweep_receipts.py 0 28` — single process, see `scripts/README.md`.
- [x] Azure Document Intelligence adapter (`src/etl/extract/adapters/azure_document_intelligence_adapter.py`, `prebuilt-receipt` model, F0 tier) — least adapter work. Azure's output is already receipt-shaped, so this is mostly field renaming. Confidence comes from DESIGN-V3.md's second origin: native, taken directly from each Azure `DocumentField.confidence`. It falls back to a corrected middling score only when Azure omits that field. No documents returned, no `TransactionDate`, or a vendor API error (`AzureError`) all route to the same total-failure `ExtractionReview` shape as the RapidOCR adapter. Tests mock the SDK client (`tests/test_azure_document_intelligence_adapter.py`).
- [x] Azure adapter swept against the same 28 real receipt photos, with live credentials. The live run found three crashes that the mocked tests never reached. Each one broke the `Extractor` Protocol's promise to return an `ExtractionReview` rather than raise. `_value()` falls back to a field's raw `content` string when Azure reports no typed value, so any field can arrive as arbitrary OCR text. One receipt returned a newline-separated fragment as its date, and another returned a bare Naira sign as a money value. `_number()` and `_date()` now coerce safely and report an unusable value as missing. Result: 24 of 28 extract cleanly and 4 route to review, against 12 clean for RapidOCR. Reproduce with `uv run python scripts/sweep_receipts_azure.py 0 28`. That script calls a paid API and uploads each photo to Microsoft, so it stays a deliberate manual tool.
- [x] Both adapters are plain classes satisfying the `Extractor` Protocol structurally — zero changes to `orchestrator.py`. Wiring them into an actual config-driven extractor list is Phase 6's job (end-to-end wiring), not this phase's.
- Deferred to Phase 7: vision-LLM adapters (Claude/GPT-4V/Gemini, Ollama-hosted models), plus EasyOCR/PaddleOCR/docTR.

---

## Phase 6 — End-to-end wiring _(depends on Phases 2, 3, 4, 5)_

- Compose orchestrator → transform → load into the full pipeline.
- Config-driven extractor selection (which extractors run for a given input).
- **Wire the orchestrator's total-failure split into the review pipeline.** `split_results()` (Phase 2, `src/etl/extract/orchestrator.py`) partitions the orchestrator's raw output into `(extraction_results, total_failure_reviews)`. Today nothing calls it and nothing consumes `total_failure_reviews` — no code merges it into the final review list that reaches `emit()`. This phase must add that merge (e.g. into `emit()`'s existing `reconcile_reviews`/`validate_reviews` inputs, or a third input), so a total-failure `ExtractionReview` row actually reaches Load instead of silently going nowhere.
- **Downscale an image before upload when a vendor rejects it for size.** The live Azure sweep showed 2 of 28 real photos exceed the `prebuilt-receipt` size limit. Both are over 4 MB. The adapter reports the vendor error as an `ExtractionReview`. That behaviour is right, but the receipts stay unread. A modern phone camera makes files this large routinely, so this is a common case rather than an edge one. `preprocess.py` already decodes and re-encodes images, so the resize belongs there rather than in the adapter. The team deferred this from Phase 5, since this work is about feeding a vendor rather than about proving the `Extractor` Protocol.
- **Integration test — the pipeline must work stage-to-stage, not just within each stage.** Unit tests exist per stage today (`test_extract.py`, `test_transform.py`, `test_load.py`). Nothing yet proves one stage's real output feeds the next. A Phase 2 audit confirmed this gap is real. `test_extract.py` never imports or calls `reconcile`/`validate`/`shape`/`emit`. Nothing proves `split_results()`'s `extraction_results` list is a valid `reconcile()` input. Nothing proves its `total_failure_reviews` list reaches the final review set. This phase's integration test must run the full chain through and through, end to end:
  1. Real image fixture → real adapters (Phase 5) → `run_extractors()` → `split_results()`.
  2. `extraction_results` → `reconcile()` → `validate()` → `shape()` → `emit()`.
  3. `total_failure_reviews` (if any fired) → confirmed present in the final review set `emit()` produces, not dropped.
  4. `EmitResult` → `LoadClient.ingest()` → mock `vela-api`. Assert on what the mock actually received (store, receipt, line_items, and the full combined `extraction_reviews`, including any total-failure rows).

  Every arrow above needs its own assertion — each assertion must prove one stage's real output shape satisfies the next stage's real input contract. Isolated unit tests do not show this.

---

## Phase 7 — Remaining adapters & hardening

- Remaining OCR adapters (EasyOCR, PaddleOCR, docTR) and vision-LLM adapters:
  - Cloud vision LLMs (Claude, GPT-4V, Gemini) via their vendor SDKs, prompted to return JSON matching the schema directly.
  - Self-hosted vision LLMs (Qwen2.5-VL, Llama 3.2 Vision) via a local Ollama install, called over Ollama's HTTP API. Ollama itself is a local runtime dependency to install, not just a Python package.
- `tenacity` retry and backoff on flaky vendor calls.
- Structured logging, via `structlog` or the built-in `logging` module.
- Confirm the confidence-derivation validation logic, such as `quantity × unit_price == line_total`, is a single shared utility. Extract's fallback-confidence origin and Transform's `validate` stage must both reuse it, not duplicate it.

---

## Testing strategy (cross-cutting)

- Unit tests per stage (Extract adapters, each Transform function, Load client) — fast, isolated.
- Fixtures: synthetic/faker-generated receipt images and data only. No real personal receipts committed to the repo, consistent with the data-minimization/privacy stance in the design doc.
- Integration tier: full pipeline against the standalone mock app (Phase 4/6).
- The team defers real `vela-api` integration until that service exists. Swapping the mock's base URL for the real one should require no code change, per the design doc's stated build order.

---

## Notes for later

- Confirm the exact path to `vela-core`'s JSON Schema files inside the submodule after this repo adds the submodule. The design doc names `docs/SCHEMA.md` and `schemas/` in `vela-core`. Confirm the real file layout directly rather than assume it. Then update the `gen:types` script in "Named commands" above if the path differs.
