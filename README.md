# vela-etl

Receipt photos in, structured data out — extraction, confidence scoring, and manual review.

## What lives here

- The pipeline that turns a receipt photo into structured data: merchant, date, line items, quantities, prices, tax, total.
- Confidence scoring on each extracted field, and a manual-review path for anything the pipeline isn't confident about.
- Extracts only the fields defined in [`vela-core`](https://github.com/devdesiignn/vela-core)'s schema — nothing beyond that is captured or retained.
- Writes structured data and review resolutions via [`vela-api`](https://github.com/devdesiignn/vela-api), rather than connecting to the database directly.

## What this does not do

- v1 handles printed receipts only. POS screenshots and handwritten receipts are out of scope for this version.
- Does not expose a public API or dashboard.
- Does not connect to `vela-core`'s database directly — all writes go through `vela-api`.
- Does not chase perfect accuracy on every receipt; extraction accuracy is tracked and reported as a number, with a manual-review path for the rest.

## Pipeline stages

Extract → Transform → Load, one Python codebase, no cross-language boundary. Extract and Transform are pure/local; Load is the only stage that makes network calls.

- **`src/etl/types/`** — `Store`, `Receipt`, `LineItem`, `ExtractionReview`: Pydantic models generated from `vela-core`'s vendored JSON Schema (`uv run poe gen:types`). Never hand-edited; regenerated after `submodule:update`.
- **Extract** (`src/etl/extract/`) — one or more extractor plug-ins turn a receipt photo into an `ExtractionResult`: candidate field values plus a per-field confidence score. Extractors share one interface; orchestration never branches on which one ran. Candidate/confidence types are defined (`extract/types.py`); the extractor interface, real adapters, and orchestrator are not implemented yet.
- **Transform** (`src/etl/transform/`) — pure functions, no I/O. Turns the extractor(s)' output into schema-shaped records plus review rows via `reconcile → validate → shape → emit`. Implemented and tested.
- **Load** (`src/etl/load/`) — `LoadClient` sends the shaped `Store`, `Receipt`, `LineItem`s, and `ExtractionReview`s to `vela-api` in one combined-payload HTTP request. No DB driver, no DB credentials. Implemented and tested against `mock_api/`, a standalone FastAPI app standing in for `vela-api` until it exists (`uv run poe mock-api`).

Nothing wires these three stages together end-to-end yet — each is tested in isolation.

## Status

Phase 1 (repo and schema foundation), Phase 3 (Transform), and Phase 4 (mock `vela-api` and Load client) are complete. Phase 2's types are in place but its extractor interface and orchestrator are not yet built. Extract's real adapters (Phase 5/7) and end-to-end wiring (Phase 6) have not started. See [`docs/IMPLEMENTATION-PLAN.md`](docs/IMPLEMENTATION-PLAN.md) for the full phase breakdown.

## Development

Managed with [uv](https://docs.astral.sh/uv/); named tasks run via [`poethepoet`](https://github.com/nat-n/poethepoet) (see `pyproject.toml`'s `[tool.poe.tasks]`):

- `uv run poe bootstrap` — first-time setup: populates `vendor/vela-core` and generates types from its schema.
- `uv run poe test` — run the test suite (self-sufficient from a cold clone; runs `bootstrap` first).
- `uv run poe lint` / `uv run poe format` — `ruff check .` / `ruff format .`.
- `uv run poe mock-api` — run the standalone `vela-api` mock locally (port `2222`).

## Related repos

Part of [Vela](https://github.com/devdesiignn/vela) (Receipt Intelligence Platform). Depends on [`vela-core`](https://github.com/devdesiignn/vela-core)'s schema, and writes through [`vela-api`](https://github.com/devdesiignn/vela-api).
