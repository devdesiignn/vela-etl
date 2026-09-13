# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

Early setup — pipeline design in progress. There is no code, build system, or test suite yet. When implementation begins, update this file with real build/lint/test commands and architecture notes (not aspirational ones).

## What this repo is

`receipt-etl` is P1 of the [Receipt Intelligence Platform](https://github.com/devdesiignn/receipt-intelligence-platform) — the ingestion/extraction pipeline. Photo in, structured data out: merchant, date, line items, quantities, prices, tax, total.

- Computes a confidence score per extracted field, with a manual-review path for anything the pipeline isn't confident about.
- Extracts only the fields defined in [`receipt-core`](https://github.com/devdesiignn/receipt-core)'s schema (`stores`, `receipts`, `line_items`, `extraction_reviews`) — nothing beyond that is captured or retained.
- **Never connects to `receipt-core`'s database directly.** All writes (new extracted receipts, review resolutions) go through [`receipt-api`](https://github.com/devdesiignn/receipt-api) — it is the only service with a direct connection to `receipt-core`.
- Built against `receipt-api`'s OpenAPI spec, pointed at a mock server implementing that spec until `receipt-api`'s real implementation exists (build order: `receipt-core` → `receipt-etl` against the mock → `receipt-api` for real, then `receipt-etl` switches over with no code change).

## Scope boundaries

- v1 handles printed receipts only. POS screenshots and handwritten receipts are explicitly out of scope — use an extractor pattern so other source types can be added later without changing the shared schema.
- No public API or dashboard is exposed by this service.
- Perfect per-receipt accuracy is not the goal — extraction accuracy is tracked/reported as a number (percent cleanly auto-extracted vs. flagged for manual review), with manual review handling the remainder.
- Privacy is data minimization, not redaction: extract only what `receipt-core`'s schema defines (required/common/rare tiers, rare fields into a structured `extras` field), so there's nothing extra to redact afterward.

## The shared data contract (`receipt-core`)

Read [`receipt-core`'s SCHEMA.md](../receipt-core/docs/SCHEMA.md) before writing any extraction logic — it defines the extractor interface this pipeline must produce.

- Successfully extracted fields go into `stores` / `receipts` / `line_items`, matching `receipt-core`'s JSON Schemas (`store.schema.json`, `receipt.schema.json`, `line_item.schema.json`).
- Anything uncertain (low-confidence field, disagreement between extraction attempts, a missed line item) goes into `extraction_reviews` instead, matching `extraction_review.schema.json`:
  - Single wrong field → `field_name` is the real column name, `line_item_id` set only if it's a line-item-level field.
  - Whole line item wrong (merged/split/invented) → `field_name = "line_item"` sentinel.
  - Missed item entirely → `field_name = "missing_line_item"` sentinel, no `line_item_id`.
  - Multiple extractors disagreeing on the same field → one row per extractor, same `receipt_id`/`line_item_id`/`field_name`, `flagged_reason = "conflicting_extractions"`. Agreement produces zero rows.
- `flagged_reason` (why the row exists) and `status` (`pending`/`resolved`/`rejected`) are independent — `rejected` is reversible, not terminal.
- `content_hash` (computed from store + transaction ref + date + total) is used by `receipt-core` to reject duplicate receipts at insert time — this pipeline must compute it consistently for the same physical receipt.

Do not treat `receipt-core`'s migration files as the contract — the JSON Schemas in `schemas/` are what this repo validates against.

## Commit conventions

- Use Conventional Commits: `type(scope): message` (e.g. `feat(extractor): add OCR confidence scoring`, `fix(pipeline): correct duplicate detection hash`, `chore(deps): bump sdk version`). Common types: `feat`, `fix`, `chore`, `docs`, `refactor`, `test`. Scope is the affected domain and is optional but preferred when a change is domain-specific.
- Do not add a `Co-Authored-By: Claude` trailer to commit messages in this repo.

## Related repos

Part of the [Receipt Intelligence Platform](https://github.com/devdesiignn/receipt-intelligence-platform) — see its `docs/master-plan.md` for full cross-repo architecture and timeline.

- [`receipt-core`](https://github.com/devdesiignn/receipt-core) — owns the schema this pipeline extracts into.
- [`receipt-api`](https://github.com/devdesiignn/receipt-api) — the only path for writing structured data and review resolutions; this pipeline is built against its OpenAPI spec.
