# Implementation Plan

Build roadmap derived from [`design/DESIGN-V3.md`](./design/DESIGN-V3.md). The plan is sequenced in dependency order. Earlier phases unblock later ones. Phases marked "parallel" have no dependency on each other and can be done in either order.

**Dependency order and effort order are not the same axis.** They point in opposite directions here. Of the three ETL stages, Load is the least non-trivial. It is one HTTP call against a combined-payload endpoint, with no branching logic of its own. Transform is the middle. It has four pure functions, but real business-logic rules to get exactly right: reconciliation, validation, the extraction_reviews sentinel cases. Extract is the most tedious by a wide margin. It needs N vendor adapters, each with its own response shape to map. It also needs image preprocessing and OCR-specific parsing by hand. It needs retry and backoff, plus three different confidence origins to reconcile.

This is why the team builds Load _early_ (Phase 4) against a mock, in parallel with Extract's skeleton. It is low-effort and worth clearing out of the way immediately, not because dependency order requires it early. Extract's real weight, the adapters themselves, is deliberately pushed later, to Phases 5 and 7. That is where the actual implementation cost sits. The phase numbers below reflect what unblocks what. They do not reflect effort. Do not read "Phase 4" as somehow harder than "Phase 7."

---

## Tooling decisions

| Decision                       | Choice                                                      | Why                                                                                                                                                  |
| ------------------------------ | ----------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| Dependency manager             | [uv](https://docs.astral.sh/uv/)                            | One tool for venv, install, and lockfile. Fastest onramp for a from-scratch Python project.                                                          |
| `vela-core` schema sourcing | Git submodule, pinned to a commit                           | Conventional git-native mechanism for a pinned, trackable snapshot of another repo's content. Git enforces the version relationship, not memory.     |
| Mock `vela-api`             | Both `respx` (unit tests) and a standalone FastAPI mock app | `respx` gates automated testing. The standalone app lets the full pipeline be run manually end-to-end before the real `vela-api` exists.          |
| CI                             | GitHub Actions running `pytest`                             | Basic regression safety net from the start                                                                                                           |
| Local enforcement              | `pre-commit` + `ruff`                                       | Matches `vela-core`'s existing convention: Husky and lint-staged running eslint and prettier before every commit. Same idea, Python-native tools. |

---

## Named commands

`vela-core` exposes every setup and build operation as a named `npm run <script>` in `package.json`. The developer never has to remember a bare shell command. `uv` has no built-in equivalent (no `[tool.uv.scripts]`), so `vela-etl` matches the pattern via [`poethepoet`](https://github.com/nat-n/poethepoet), a task runner configured entirely in `pyproject.toml`'s `[tool.poe.tasks]` and invoked as `uv run poe <task>`:

| Task               | Command                                                                                                                                                                  | Purpose                                                                                                    |
| ------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------- |
| `submodule:init`   | `git submodule update --init --recursive`                                                                                                                                | First-time setup after cloning — populates `vendor/vela-core` at its pinned commit                      |
| `submodule:update` | `git -C vendor/vela-core fetch && git -C vendor/vela-core checkout <new-commit> && git add vendor/vela-core`                                                    | Deliberately bump the pinned schema version. Stages the new submodule pointer for commit in `vela-etl`. |
| `gen:types`        | `datamodel-codegen --input vendor/vela-core/schemas --input-file-type jsonschema --output src/etl/types --output-model-type pydantic_v2.BaseModel --formatters black` | Regenerate Python types from the vendored JSON Schema. Run after `submodule:update`, or on first setup.    |
| `bootstrap`        | `uv run poe submodule:init && uv run poe gen:types`                                                                                                                      | Single command from a cold clone to a working local setup — mirrors `vela-core`'s `bootstrap:dev`       |
| `test`             | `pytest`                                                                                                                                                                 | Run the test suite                                                                                         |
| `lint`             | `ruff check .`                                                                                                                                                           | Lint                                                                                                       |
| `format`           | `ruff format .`                                                                                                                                                          | Format                                                                                                     |

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

`src/` is the standard Python "src layout" convention — it prevents tests from accidentally importing an uninstalled local copy of the package instead of the properly installed one. The importable package name is `etl`, since this repo builds exactly one pipeline and the extra prefix would be redundant inside it.

---

## Phase 1 — Repo & schema foundation _(no dependencies)_ — ✅ done

- [x] `uv init`, set up `pyproject.toml`, lockfile. Named commands added to `pyproject.toml`'s `[tool.poe.tasks]` (see "Named commands" above): `submodule:init`, `submodule:update`, `gen:types`, `bootstrap`, `test`, `lint`, `format`.
- [x] Added `vela-core` as a git submodule at `vendor/vela-core`, pinned to its commit at add-time. First-time setup is `uv run poe submodule:init`. Deliberately bumping the pinned schema version later is `uv run poe submodule:update`.
- [x] `uv run poe gen:types` runs `datamodel-codegen` against `vendor/vela-core/schemas/*.schema.json`, output into `src/etl/types/`. Confirmed schema path matches `vela-core`'s `docs/SCHEMA.md`.
- [x] Round-trip test (`tests/test_generated_types.py`): generated types construct a valid instance and reject an invalid one (missing required field) — covers `Store` and `Receipt` (with nested `LineItem`).
- [x] `pre-commit` set up with `ruff-check --fix` + `ruff-format`, matching `vela-core`'s lint-staged convention. Hook installed and verified to actually rewrite bad code.
- [x] `.github/workflows/ci.yml` set up: runs `uv run poe lint` and `uv run poe test` on push/PR, with submodule checkout.

**Blocks:** everything else. Extract, Transform, and Load all use the generated types.

**Not yet started:** Phases 2–7 (Extract, Transform, Load, wiring, hardening).

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
- `validate`, and re-validation against `vela-core`'s JSON Schema before Load, uses `pydantic` or `jsonschema`, per the design doc's tool inventory. Pick one as the primary validator. Both may end up in use for different purposes: `pydantic` for shape and types, `jsonschema` for validating raw dicts against the vendored schema files directly. If so, document why in code comments.
- Test with hand-built `ExtractionResult` fixtures — no real extractors, no `vela-api`, needed to exercise this stage.
- Cover: single extractor (pass-through), agreeing extractors (zero review rows), disagreeing extractors (one review row per extractor), validation failures, total-failure input.

---

## Phase 4 — Mock vela-api & Load client _(depends on Phase 1, blocks nothing else)_

- Sketch a minimal OpenAPI spec for the combined-payload write endpoint. Per `vela-core`'s `docs/SCHEMA.md`, the extractor interface has two sides, and the payload must carry both. Successfully extracted data goes into `stores` / `receipts` / `line_items` together. `store` is not a separate concern from `receipt`. Both belong in the same success path, since the pipeline starts from a bare photo with no pre-existing store row to reference. Anything uncertain or wrong (low confidence, disagreement, a missed item) goes into `extraction_reviews` instead, for the same underlying extracted content. So the payload has two parts, written atomically: store + receipt + nested line_items for the success path, and extraction_reviews for the uncertain/failure path. `DESIGN-V3.md`'s Load section omits `store` from its version of this list. Confirm and correct there too before finalizing the spec.
- `vela-etl` sends the extracted store data with each receipt, unconditionally. It has no database connection, per this repo's own architecture.
- Build the standalone FastAPI mock app (`mock_api/`) implementing that spec — success path, "already exists" (duplicate `content_hash`) response, validation-error response.
- Build the `httpx`-based Load client in `src/etl/load/` against the spec.
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
- The team defers real `vela-api` integration until that service exists. Swapping the mock's base URL for the real one should require no code change, per the design doc's stated build order.

---

## Notes for later

- Confirm the exact path to `vela-core`'s JSON Schema files inside the submodule once the submodule is added. The design doc references `docs/SCHEMA.md` and `schemas/` in `vela-core`, but check the concrete file layout directly rather than assume it. Update the `gen:types` script in "Named commands" above if the path differs.
