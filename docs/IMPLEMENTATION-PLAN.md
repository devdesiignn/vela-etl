# Implementation Plan

Build roadmap derived from [`design/DESIGN-V3.md`](./design/DESIGN-V3.md). The plan is sequenced in dependency order. Earlier phases unblock later ones. Phases marked "parallel" have no dependency on each other and can be done in either order.

**Dependency order and effort order are not the same axis.** They point in opposite directions here. Of the three ETL stages, Load is the least non-trivial. It is one HTTP call against a combined-payload endpoint, with no branching logic of its own. Transform is the middle. It has four pure functions, but real business-logic rules to get exactly right: reconciliation, validation, the extraction_reviews sentinel cases. Extract is the most tedious by a wide margin. It needs N vendor adapters, each with its own response shape to map. It also needs image preprocessing and OCR-specific parsing by hand. It needs retry and backoff, plus three different confidence origins to reconcile.

This is why the team builds Load _early_ (Phase 4) against a mock, in parallel with Extract's skeleton. It is low-effort and worth clearing out of the way immediately, not because dependency order requires it early. Extract's real weight, the adapters themselves, is deliberately pushed later, to Phases 5 and 7. That is where the actual implementation cost sits. The phase numbers below reflect what unblocks what. They do not reflect effort. Do not read "Phase 4" as somehow harder than "Phase 7."

---

## Tooling decisions

| Decision                       | Choice                                                      | Why                                                                                                                                                  |
| ------------------------------ | ----------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| Dependency manager             | [uv](https://docs.astral.sh/uv/)                            | One tool for venv, install, and lockfile. Fastest onramp for a from-scratch Python project.                                                          |
| `receipt-core` schema sourcing | Git submodule, pinned to a commit                           | Conventional git-native mechanism for a pinned, trackable snapshot of another repo's content. Git enforces the version relationship, not memory.     |
| Mock `receipt-api`             | Both `respx` (unit tests) and a standalone FastAPI mock app | `respx` gates automated testing. The standalone app lets the full pipeline be run manually end-to-end before the real `receipt-api` exists.          |
| CI                             | GitHub Actions running `pytest`                             | Basic regression safety net from the start                                                                                                           |
| Local enforcement              | `pre-commit` + `ruff`                                       | Matches `receipt-core`'s existing convention: Husky and lint-staged running eslint and prettier before every commit. Same idea, Python-native tools. |

---

## Named commands

`receipt-core` exposes every setup and build operation as a named `npm run <script>` in `package.json`. The developer never has to remember a bare shell command. `receipt-etl` matches this via `pyproject.toml`'s `[tool.uv.scripts]`, uv's task-runner equivalent, invoked as `uv run <script>`:

| Script             | Command                                                                                                                | Purpose                                                                                                    |
| ------------------ | ---------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| `submodule:init`   | `git submodule update --init --recursive`                                                                              | First-time setup after cloning — populates `vendor/receipt-core` at its pinned commit                      |
| `submodule:update` | `cd vendor/receipt-core && git fetch && git checkout <new-commit> && cd ../.. && git add vendor/receipt-core`          | Deliberately bump the pinned schema version. Stages the new submodule pointer for commit in `receipt-etl`. |
| `gen:types`        | `datamodel-code-generator --input vendor/receipt-core/schemas --input-file-type jsonschema --output receipt_etl/types` | Regenerate Python types from the vendored JSON Schema. Run after `submodule:update`, or on first setup.    |
| `bootstrap`        | `uv run submodule:init && uv run gen:types`                                                                            | Single command from a cold clone to a working local setup — mirrors `receipt-core`'s `bootstrap:dev`       |
| `test`             | `pytest`                                                                                                               | Run the test suite                                                                                         |
| `lint`             | `ruff check .`                                                                                                         | Lint                                                                                                       |
| `format`           | `ruff format .`                                                                                                        | Format                                                                                                     |

`pretest` should run `bootstrap` the same way `receipt-core`'s `pretest` runs `bootstrap:dev`. This makes `uv run test` self-sufficient from a cold clone, with no manual setup steps to remember or document separately.

---

## Project scaffolding

```txt
receipt-etl/
├── receipt_etl/
│   ├── types/           # generated from receipt-core's JSON Schema
│   ├── extract/         # extractor interface + adapters
│   ├── transform/        # reconcile, validate, shape, emit
│   ├── load/             # httpx client for receipt-api
│   └── config.py
├── mock_api/             # standalone FastAPI app implementing the sketched OpenAPI spec
├── vendor/receipt-core/  # git submodule, pinned commit
├── scripts/
│   └── gen_types.sh      # runs datamodel-code-generator against vendor/receipt-core schemas
├── tests/
├── .pre-commit-config.yaml
├── .github/workflows/ci.yml
└── pyproject.toml        # uv-managed
```

---

## Phase 1 — Repo & schema foundation _(no dependencies)_

- `uv init`, set up `pyproject.toml`, lockfile. Add the `[tool.uv.scripts]` entries from "Named commands" above: `submodule:init`, `submodule:update`, `gen:types`, `bootstrap`, `test`, `lint`, `format`.
- Add `receipt-core` as a git submodule: `git submodule add https://github.com/devdesiignn/receipt-core.git vendor/receipt-core`, then pin it to a specific commit. From then on, first-time setup is `uv run submodule:init`. Deliberately bumping the pinned schema version later is `uv run submodule:update`.
- `uv run gen:types` runs `datamodel-code-generator` against `vendor/receipt-core/schemas/*.schema.json`, output into `receipt_etl/types/`. Confirm the exact schema path once the submodule is added — `receipt-core`'s `docs/SCHEMA.md` documents the schema location.
- Round-trip test: generated types can construct a valid instance and reject an invalid one (e.g. missing required field).
- Set up `pre-commit` with `ruff` (lint + format), matching `receipt-core`'s lint-staged convention.
- Set up `.github/workflows/ci.yml`: run `pytest` (and `ruff check`) on push/PR.

**Blocks:** everything else. Extract, Transform, and Load all use the generated types.

---

## Phase 2 — Extractor interface & orchestrator _(depends on Phase 1)_

- Define `ExtractionResult` type using `pydantic` (shape validation for candidate data, per the design doc's Extract/Transform tool inventory) and the extractor `Protocol` (`extract(image) -> ExtractionResult`).
- Orchestrator: takes a config-driven list of extractors, runs each, returns the results list unchanged in shape regardless of count (1 or N).
- Total-failure path: an extractor can return an `extraction_reviews`-shaped row directly instead of an `ExtractionResult`.
- Test against hand-written stub extractors only — no real OCR/vision-LLM adapters yet. This isolates orchestration logic from adapter correctness.

---

## Phase 3 — Transform pipeline _(depends on Phase 1 only — parallel to Phase 2)_

- Implement `reconcile`, `validate`, `shape`, `emit` as pure functions per the design doc's rules (sentinels, `flagged_reason` values, `line_order`).
- `shape` computes `content_hash` via stdlib `hashlib` as a pure function of `store_id + transaction_ref + date + total`.
- `validate`, and re-validation against `receipt-core`'s JSON Schema before Load, uses `pydantic` or `jsonschema`, per the design doc's tool inventory. Pick one as the primary validator. Both may end up in use for different purposes: `pydantic` for shape and types, `jsonschema` for validating raw dicts against the vendored schema files directly. If so, document why in code comments.
- Test with hand-built `ExtractionResult` fixtures — no real extractors, no `receipt-api`, needed to exercise this stage.
- Cover: single extractor (pass-through), agreeing extractors (zero review rows), disagreeing extractors (one review row per extractor), validation failures, total-failure input.

---

## Phase 4 — Mock receipt-api & Load client _(depends on Phase 1, blocks nothing else)_

- Sketch a minimal OpenAPI spec for the combined-payload write endpoint (receipt + nested line_items + reviews, atomic).
- Build the standalone FastAPI mock app (`mock_api/`) implementing that spec — success path, "already exists" (duplicate `content_hash`) response, validation-error response.
- Build the `httpx`-based Load client in `receipt_etl/load/` against the spec.
- Unit tests via `respx`, mocking the same success/duplicate/validation-error paths.
- Manual smoke test: run the mock app locally, point the Load client at it, confirm a real HTTP round trip.

---

## Phase 5 — First real adapters _(depends on Phase 2)_

- Implement two adapters to prove both ends of the adapter-effort spectrum:
  - One cloud receipt-specific vendor (AWS Textract or Azure Document Intelligence) — least adapter work, output already receipt-shaped.
  - `pytesseract` — most adapter work, raw text requiring manual mapping into the candidate shape.
- Add `OpenCV`-based image preprocessing, such as deskew and contrast/threshold cleanup, ahead of the OCR adapter. General-purpose OCR accuracy is sensitive to image quality. This is Extract-stage work per the design doc's tool inventory, not deferred to a later phase.
- Defer vision-LLM adapters (Claude/GPT-4V/Gemini, Ollama-hosted models) to Phase 7.

---

## Phase 6 — End-to-end wiring _(depends on Phases 2, 3, 4, 5)_

- Compose orchestrator → transform → load into the full pipeline.
- Config-driven extractor selection (which extractors run for a given input).
- Integration test: run a real image fixture through the real adapters from Phase 5, then transform, then Load against the mock app. Assert on what the mock received.

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
- The team defers real `receipt-api` integration until that service exists. Swapping the mock's base URL for the real one should require no code change, per the design doc's stated build order.

---

## Notes for later

- Confirm the exact path to `receipt-core`'s JSON Schema files inside the submodule once the submodule is added. The design doc references `docs/SCHEMA.md` and `schemas/` in `receipt-core`, but check the concrete file layout directly rather than assume it. Update the `gen:types` script in "Named commands" above if the path differs.
