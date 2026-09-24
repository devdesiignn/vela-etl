# vela-etl

Receipt photos in, structured data out — extraction, confidence scoring, and manual review.

## What lives here

- The pipeline that turns a receipt photo into structured data: merchant, date, line items, quantities, prices, tax, total.
- Confidence scoring on each extracted field, and a manual-review path for anything the pipeline isn't confident about.
- Extracts only the fields [`vela-core`](https://github.com/devdesiignn/vela-core)'s schema defines — it captures and retains nothing beyond that.
- Writes structured data and review resolutions via [`vela-api`](https://github.com/devdesiignn/vela-api), rather than connecting to the database directly.

## What this does not do

- v1 handles printed receipts only. POS screenshots and handwritten receipts are out of scope for this version.
- Does not expose a public API or dashboard.
- Does not connect to `vela-core`'s database directly — all writes go through `vela-api`.
- Does not chase perfect accuracy on every receipt. The pipeline tracks and reports extraction accuracy as a number, with a manual-review path for the rest.

## Pipeline stages

Extract → Transform → Load, one Python codebase, no cross-language boundary. Extract and Transform are pure and local. Load is the only stage that makes network calls.

- **`src/etl/types/`** — `Store`, `Receipt`, `LineItem`, `ExtractionReview`: Pydantic models generated from `vela-core`'s vendored JSON Schema (`uv run poe gen:types`). No one hand-edits these. `uv run poe gen:types` regenerates them after `submodule:update`.
- **Extract** (`src/etl/extract/`) — one or more extractor plug-ins turn a receipt photo into an `ExtractionResult`: candidate field values plus a per-field confidence score. Extractors share one interface. Orchestration never branches on which one ran. Implemented: the extractor interface and orchestrator (Phase 2). Also implemented: two adapters proving both ends of the adapter-effort spectrum (Phase 5). One is Azure Document Intelligence (`prebuilt-receipt`, cloud, already receipt-shaped). The other is a RapidOCR-based adapter (raw OCR text, mapped by hand). Remaining adapters (EasyOCR, PaddleOCR, docTR, vision-LLMs) are Phase 7.
- **Transform** (`src/etl/transform/`) — pure functions, no I/O. Turns the extractor(s)' output into schema-shaped records plus review rows via `reconcile → validate → shape → emit`. Implemented and tested.
- **Load** (`src/etl/load/`) — `LoadClient` sends the shaped `Store`, `Receipt`, `LineItem`s, and `ExtractionReview`s to `vela-api` in one combined-payload HTTP request. No DB driver, no DB credentials. Implemented and tested against `mock_api/`, a standalone FastAPI app standing in for `vela-api` until it exists (`uv run poe mock-api`).

Nothing wires these three stages together end-to-end yet. Each stage's own tests cover it in isolation.

## Status

Phases 1 through 5 are complete. Done: repo and schema foundation, the extractor interface and orchestrator, Transform, the mock `vela-api` and Load client, and two real Extract adapters (Azure Document Intelligence, RapidOCR). Not started: end-to-end wiring (Phase 6) and the remaining adapters (Phase 7). See [`docs/IMPLEMENTATION-PLAN.md`](docs/IMPLEMENTATION-PLAN.md) for the full phase breakdown.

## System requirements

`uv sync` installs everything. No Extract adapter needs a system package: the OCR adapter uses `rapidocr` (with `onnxruntime`), a plain PyPI package that runs its ONNX models in-process. RapidOCR caches its model weights under the package install after the first run.

The Azure Document Intelligence adapter is a plain HTTPS API call. It does need `AZURE_DOC_INTEL_ENDPOINT` and `AZURE_DOC_INTEL_KEY` set — see `.env.example`.

## Development

[uv](https://docs.astral.sh/uv/) manages this project. [`poethepoet`](https://github.com/nat-n/poethepoet) runs its named tasks (see `pyproject.toml`'s `[tool.poe.tasks]`):

- `uv run poe bootstrap` — first-time setup: populates `vendor/vela-core` and generates types from its schema.
- `uv run poe test` — run the test suite. It runs `bootstrap` first, so it works from a cold clone with no setup step.
- `uv run poe lint` / `uv run poe format` — `ruff check .` / `ruff format .`.
- `uv run poe mock-api` — run the standalone `vela-api` mock locally (port `2222`).

## Related repos

Part of [Vela](https://github.com/devdesiignn/vela) (Receipt Intelligence Platform). Depends on [`vela-core`](https://github.com/devdesiignn/vela-core)'s schema, and writes through [`vela-api`](https://github.com/devdesiignn/vela-api).
