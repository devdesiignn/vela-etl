# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

Design is finalized. Implementation is underway. Phase 1 (repo and schema foundation) is complete. Extract, Transform, and Load have not started. The full design is [`docs/design/DESIGN-V3.md`](docs/design/DESIGN-V3.md), authoritative and superseding V1/V2, which the repo keeps only for history. The build sequence is [`docs/IMPLEMENTATION-PLAN.md`](docs/IMPLEMENTATION-PLAN.md). [`docs/DECISIONS.md`](docs/DECISIONS.md) logs non-obvious choices and their rejected alternatives.

## Build, lint, and test

Managed with [uv](https://docs.astral.sh/uv/). Named commands run via [`poethepoet`](https://github.com/nat-n/poethepoet) (`uv` has no built-in task runner), defined in `pyproject.toml`'s `[tool.poe.tasks]`:

- `uv run poe bootstrap` — first-time setup from a fresh clone: populates `vendor/vela-core` and generates Python types from its JSON Schema.
- `uv run poe test` — run the test suite (`pytest`). Runs `bootstrap` first via `pretest`, so it's self-sufficient from a cold clone.
- `uv run poe lint` — lint (`ruff check .`).
- `uv run poe format` — format (`ruff format .`).
- `uv run poe gen:types` — regenerate Python types from the vendored JSON Schema. Run after `submodule:update`.
- `uv run poe submodule:init` / `uv run poe submodule:update` — populate the submodule at its pinned commit, or deliberately bump that pin.

The importable package is `etl`, living at `src/etl/` (standard Python src-layout). `pre-commit` runs `ruff check --fix` and `ruff format` automatically on every commit — install it once per clone with `uv run pre-commit install`.

## What this repo is

`vela-etl` is P1 of [Vela](https://github.com/devdesiignn/vela) (Receipt Intelligence Platform) — the ingestion/extraction pipeline. Photo in, structured data out: merchant, date, line items, quantities, prices, tax, total.

- **Language: Python**, whole pipeline (Extract, Transform, Load). One codebase, no cross-language boundary between stages. See `docs/DECISIONS.md` for why, instead of TypeScript, the original draft assumption.
- Computes a confidence score per extracted field, with a manual-review path for anything the pipeline isn't confident about.
- Extracts only the fields defined in [`vela-core`](https://github.com/devdesiignn/vela-core)'s schema: `stores`, `receipts`, `line_items`, `extraction_reviews`. Nothing beyond that is captured or retained.
- **Never connects to `vela-core`'s database directly.** All writes, meaning new extracted receipts, go through [`vela-api`](https://github.com/devdesiignn/vela-api). This happens via a single combined-payload endpoint: receipt, line_items, and reviews, atomic. `vela-api` is the only service with a direct connection to `vela-core`. Review *resolution* is entirely `vela-api`'s job. This pipeline never touches it.
- Built against `vela-api`'s OpenAPI spec. It points at a mock server implementing that spec until `vela-api`'s real implementation exists. Build order: `vela-core` first, `vela-etl` against the mock second, `vela-api` for real third. `vela-etl` then switches over with no code change.
- **No interconnection beyond `vela-api`.** Not `vela-search`, not `vela-agent`, not `vela-forecast`, not `vela-infra`. The platform's own guiding principle states that each repo stands alone. Nothing about how a sibling service consumes data downstream should ever leak into this repo's design.
- Extractors are plug-in units behind one shared interface: `extract(image) -> ExtractionResult`. Orchestration, reconciliation, and everything downstream never branches on which extractor ran, how many ran, or why. Adding a new vendor adapter is purely additive.

## Scope boundaries

- v1 handles printed receipts only. POS screenshots and handwritten receipts are explicitly out of scope. Use an extractor pattern so other source types can be added later without changing the shared schema.
- This service exposes no public API or dashboard.
- Perfect per-receipt accuracy is not the goal. The pipeline tracks and reports extraction accuracy as a number: percent cleanly auto-extracted versus flagged for manual review. Manual review handles the remainder.
- Privacy is data minimization, not redaction. Extract only what `vela-core`'s schema defines, in required, common, and rare tiers, with rare fields going into a structured `extras` field. There is nothing extra to redact afterward.

## The shared data contract (`vela-core`)

Read [`vela-core`'s SCHEMA.md](../vela-core/docs/SCHEMA.md) before writing any extraction logic. It defines the extractor interface this pipeline must produce. `vela-core`'s JSON Schema files are vendored into this repo as a git submodule, `vendor/vela-core`, pinned to a commit — see `docs/DECISIONS.md`. This repo uses them to generate its own Python types via `datamodel-code-generator`. Both `vela-etl` and `vela-api` validate against this schema independently. `vela-etl`'s check is a fail-fast optimization, not the enforcement mechanism.

**Standing rule: at the start of every new session, before any other work, update the submodule pin.** Run `git -C vendor/vela-core fetch`, then compare against the pinned commit (`git -C vendor/vela-core log --oneline -1`). If `vela-core` moved past the pinned commit, run `uv run poe submodule:update` and regenerate types immediately, then tell the user what changed. Do this at the start of every session, not just once — the pin exists to prevent silent drift, not to freeze the schema forever.

- Successfully extracted fields go into `stores` / `receipts` / `line_items`, matching `vela-core`'s JSON Schemas (`store.schema.json`, `receipt.schema.json`, `line_item.schema.json`).
- Anything uncertain (low-confidence field, disagreement between extraction attempts, a missed line item) goes into `extraction_reviews` instead, matching `extraction_review.schema.json`:
  - Single wrong field → `field_name` is the real column name, `line_item_id` set only if it's a line-item-level field.
  - Whole line item wrong (merged/split/invented) → `field_name = "line_item"` sentinel.
  - Missed item entirely → `field_name = "missing_line_item"` sentinel, no `line_item_id`.
  - Multiple extractors disagreeing on the same field → one row per extractor, same `receipt_id`/`line_item_id`/`field_name`, `flagged_reason = "conflicting_extractions"`. Agreement produces zero rows.
- `flagged_reason` (why the row exists) and `status` (`pending`/`resolved`/`rejected`) are independent — `rejected` is reversible, not terminal.
- `content_hash` is computed from store, transaction ref, date, and total. `vela-core` uses it to reject duplicate receipts at insert time. This pipeline must compute it consistently for the same physical receipt.

Do not treat `vela-core`'s migration files as the contract. This repo validates against the JSON Schemas in `schemas/` instead.

## Writing style

Docs and comments in this repo follow ASD-STE100 (Simplified Technical English): short sentences, one idea per sentence, no semicolons, active voice preferred. The `asd-ste100` skill is installed locally to check this. Run its linter with `python .agents/skills/asd-ste100/scripts/ste-lint.py <file>` before treating prose changes as final. `.agents/` and `.claude/` hold the installed skill files and are gitignored, since they are local tooling, not project source. `skills-lock.json` is tracked, since it records which skill came from where and lets the install be reproduced.

## Commit conventions

- Use Conventional Commits: `type(scope): message` (e.g. `feat(extractor): add OCR confidence scoring`, `fix(pipeline): correct duplicate detection hash`, `chore(deps): bump sdk version`). Common types: `feat`, `fix`, `chore`, `docs`, `refactor`, `test`. Scope is the affected domain and is optional but preferred when a change is domain-specific.
- Do not add a `Co-Authored-By: Claude` trailer to commit messages in this repo.

## PR summary convention

Two styles, depending on the nature of the PR.

**Style 1 — Technical / file-level** (infrastructure, refactors, config, tooling). Use when the PR mainly changes how the pieces connect: new dependencies, config, CI, project scaffolding. Lead each section with a Conventional Commits tag. Bullet specific files, functions, or flags changed.

```md
## What changed

**`feat(load)`: Short description of the core change**
- Specific file or function changed and what happened to it.
- Another specific change with enough detail to understand without reading the diff.

**`docs(plan)`: Short description**
- Specific file: what was changed and why.
```

**Style 2 — Pipeline behavior / data-contract level** (extraction logic, reconciliation rules, schema handling, review-row generation). Use when the PR changes what the pipeline actually does with a receipt: a new extractor, a changed confidence rule, a new `extraction_reviews` case. Lead each section with a plain heading. Explain the behavior and why, not which files moved.

```md
## What changed

### Section heading
One or two sentences explaining what changed and why — the behavior, not the file.

### Another section
Same pattern.
```

**Delivery format.** When asked for a PR summary, always return **both**:

1. A PR title (short, imperative, under ~70 chars) — separate from the summary body, never a commit message. No `type(scope):` prefix, and never a commit subject copied verbatim even if the branch has one commit.
2. The summary body, in a single copyable markdown block, using Style 1 or Style 2 as appropriate. Diffstat scope matters: base the summary on `main...<branch>`, not on the full branch history if `main` has since moved.

## Related repos

Part of [Vela](https://github.com/devdesiignn/vela) (Receipt Intelligence Platform) — see its `docs/MASTER-PLAN.md` for full cross-repo architecture and timeline.

- [`vela-core`](https://github.com/devdesiignn/vela-core) — owns the schema this pipeline extracts into.
- [`vela-api`](https://github.com/devdesiignn/vela-api) — the only path for writing structured data and review resolutions. This pipeline is built against its OpenAPI spec.
