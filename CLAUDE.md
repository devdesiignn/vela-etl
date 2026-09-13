# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

Design is finalized. Implementation has not started yet. The full design is [`docs/design/DESIGN-V3.md`](docs/design/DESIGN-V3.md), authoritative and superseding V1/V2, which the repo keeps only for history. The build sequence is [`docs/IMPLEMENTATION-PLAN.md`](docs/IMPLEMENTATION-PLAN.md). [`docs/DECISIONS.md`](docs/DECISIONS.md) logs non-obvious choices and their rejected alternatives. When implementation begins, update this file with real build, lint, and test commands, not aspirational ones.

## What this repo is

`receipt-etl` is P1 of the [Receipt Intelligence Platform](https://github.com/devdesiignn/receipt-intelligence-platform) — the ingestion/extraction pipeline. Photo in, structured data out: merchant, date, line items, quantities, prices, tax, total.

- **Language: Python**, whole pipeline (Extract, Transform, Load). One codebase, no cross-language boundary between stages. See `docs/DECISIONS.md` for why, instead of TypeScript, the original draft assumption.
- Computes a confidence score per extracted field, with a manual-review path for anything the pipeline isn't confident about.
- Extracts only the fields defined in [`receipt-core`](https://github.com/devdesiignn/receipt-core)'s schema: `stores`, `receipts`, `line_items`, `extraction_reviews`. Nothing beyond that is captured or retained.
- **Never connects to `receipt-core`'s database directly.** All writes, meaning new extracted receipts, go through [`receipt-api`](https://github.com/devdesiignn/receipt-api). This happens via a single combined-payload endpoint: receipt, line_items, and reviews, atomic. `receipt-api` is the only service with a direct connection to `receipt-core`. Review *resolution* is entirely `receipt-api`'s job. This pipeline never touches it.
- Built against `receipt-api`'s OpenAPI spec. It points at a mock server implementing that spec until `receipt-api`'s real implementation exists. Build order: `receipt-core` first, `receipt-etl` against the mock second, `receipt-api` for real third. `receipt-etl` then switches over with no code change.
- **No interconnection beyond `receipt-api`.** Not `receipt-search`, not `receipt-agent`, not `receipt-forecast`, not `receipt-infra`. The platform's own guiding principle states that each repo stands alone. Nothing about how a sibling service consumes data downstream should ever leak into this repo's design.
- Extractors are plug-in units behind one shared interface: `extract(image) -> ExtractionResult`. Orchestration, reconciliation, and everything downstream never branches on which extractor ran, how many ran, or why. Adding a new vendor adapter is purely additive.

## Scope boundaries

- v1 handles printed receipts only. POS screenshots and handwritten receipts are explicitly out of scope. Use an extractor pattern so other source types can be added later without changing the shared schema.
- This service exposes no public API or dashboard.
- Perfect per-receipt accuracy is not the goal. The pipeline tracks and reports extraction accuracy as a number: percent cleanly auto-extracted versus flagged for manual review. Manual review handles the remainder.
- Privacy is data minimization, not redaction. Extract only what `receipt-core`'s schema defines, in required, common, and rare tiers, with rare fields going into a structured `extras` field. There is nothing extra to redact afterward.

## The shared data contract (`receipt-core`)

Read [`receipt-core`'s SCHEMA.md](../receipt-core/docs/SCHEMA.md) before writing any extraction logic. It defines the extractor interface this pipeline must produce. `receipt-core`'s JSON Schema files are vendored into this repo as a git submodule, `vendor/receipt-core`, pinned to a commit — see `docs/DECISIONS.md`. This repo uses them to generate its own Python types via `datamodel-code-generator`. Both `receipt-etl` and `receipt-api` validate against this schema independently. `receipt-etl`'s check is a fail-fast optimization, not the enforcement mechanism.

- Successfully extracted fields go into `stores` / `receipts` / `line_items`, matching `receipt-core`'s JSON Schemas (`store.schema.json`, `receipt.schema.json`, `line_item.schema.json`).
- Anything uncertain (low-confidence field, disagreement between extraction attempts, a missed line item) goes into `extraction_reviews` instead, matching `extraction_review.schema.json`:
  - Single wrong field → `field_name` is the real column name, `line_item_id` set only if it's a line-item-level field.
  - Whole line item wrong (merged/split/invented) → `field_name = "line_item"` sentinel.
  - Missed item entirely → `field_name = "missing_line_item"` sentinel, no `line_item_id`.
  - Multiple extractors disagreeing on the same field → one row per extractor, same `receipt_id`/`line_item_id`/`field_name`, `flagged_reason = "conflicting_extractions"`. Agreement produces zero rows.
- `flagged_reason` (why the row exists) and `status` (`pending`/`resolved`/`rejected`) are independent — `rejected` is reversible, not terminal.
- `content_hash` is computed from store, transaction ref, date, and total. `receipt-core` uses it to reject duplicate receipts at insert time. This pipeline must compute it consistently for the same physical receipt.

Do not treat `receipt-core`'s migration files as the contract. This repo validates against the JSON Schemas in `schemas/` instead.

## Writing style

Docs and comments in this repo follow ASD-STE100 (Simplified Technical English): short sentences, one idea per sentence, no semicolons, active voice preferred. The `asd-ste100` skill is installed locally to check this. Run its linter with `python .agents/skills/asd-ste100/scripts/ste-lint.py <file>` before treating prose changes as final. `.agents/` and `.claude/` hold the installed skill files and are gitignored, since they are local tooling, not project source. `skills-lock.json` is tracked, since it records which skill came from where and lets the install be reproduced.

## Commit conventions

- Use Conventional Commits: `type(scope): message` (e.g. `feat(extractor): add OCR confidence scoring`, `fix(pipeline): correct duplicate detection hash`, `chore(deps): bump sdk version`). Common types: `feat`, `fix`, `chore`, `docs`, `refactor`, `test`. Scope is the affected domain and is optional but preferred when a change is domain-specific.
- Do not add a `Co-Authored-By: Claude` trailer to commit messages in this repo.

## Related repos

Part of the [Receipt Intelligence Platform](https://github.com/devdesiignn/receipt-intelligence-platform) — see its `docs/master-plan.md` for full cross-repo architecture and timeline.

- [`receipt-core`](https://github.com/devdesiignn/receipt-core) — owns the schema this pipeline extracts into.
- [`receipt-api`](https://github.com/devdesiignn/receipt-api) — the only path for writing structured data and review resolutions. This pipeline is built against its OpenAPI spec.
