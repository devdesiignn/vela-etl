# Design Notes v3 (Final)

Supersedes [`DESIGN-V1.md`](./DESIGN-V1.md) (v1) and [`DESIGN-V2.md`](./DESIGN-V2.md) (v2) in full. This is the authoritative design reference going forward. v1 and v2 remain for history only. Do not use them to answer "what did we decide" questions — this document is the answer.

---

## Overview

`vela-etl` is P1 of [Vela](https://github.com/devdesiignn/vela) (Receipt Intelligence Platform): photo in, structured data out. It extracts merchant, date, line items, quantities, prices, tax, and total from printed receipt photos. It scores confidence per field and routes anything uncertain to manual review. It never touches a database directly. All writes go through `vela-api`.

---

## Architecture

- `vela-etl` does **not** connect to `vela-core`'s database directly. No DB driver, no DB credentials anywhere in this codebase.
- All writes (new extracted receipts, and nothing else — review _resolution_ is `vela-api`'s job, not `vela-etl`'s) go through `vela-api` over HTTP.
- `vela-etl` extracts **only** the fields defined in `vela-core`'s schema (`stores`, `receipts`, `line_items`, `extraction_reviews`). Nothing beyond that is captured or retained.
- Contract with `vela-api` is an OpenAPI spec. Until `vela-api` exists for real, `vela-etl` builds and tests Load against a mock implementing that same spec. It switches over later with no code change. Build order: `vela-core` first. `vela-etl` against the mock second. `vela-api` for real third.
- **No interconnection beyond this one contract.** `vela-etl` is P1 of a six-repo platform: `vela-core`, `vela-etl`, `vela-api`, `vela-search`, `vela-agent`, `vela-forecast`, `vela-infra`. It has no dependency on and no awareness of any repo besides `vela-api`. Not `vela-search`. Not `vela-agent`. Not `vela-forecast`. Not `vela-infra`. The platform's own guiding principle states this directly. It says: "each repo stands alone... someone should be able to open any one of the six repos, without reading the other five, and understand what it does and why." `vela-etl` faces exactly one problem: image in, structured data out, handed to `vela-api`. Nothing about how any sibling service consumes that data downstream should ever leak into this repo's design.

---

## Language: Python (whole pipeline)

**Decision:** Extract, Transform, and Load all run as a single Python codebase. No subprocess calls, no HTTP hops, no cross-language boundary between the three stages.

**Why:**

- Extract carries the pipeline's real complexity and compute cost: OCR, vision-LLM calls, confidence scoring, fallback logic, per-vendor adapters. Python's OCR/ML ecosystem is native and mature here — EasyOCR, PaddleOCR, docTR, and `pytesseract` all run in-process. The TypeScript equivalents are thin or nonexistent and would require a separate Python service anyway.
- Transform (reconcile, validate, shape, emit) and Load (HTTP calls to `vela-api`) are both thin, language-neutral steps with no technical pull toward another language. Extract already needs Python, so keeping everything in one language avoids introducing a cross-language boundary purely for stages that don't need one.
- Node/TypeScript plays no runtime role anywhere in this pipeline.

---

## Contract with vela-api

Two separate things:

1. **Data shape** — what a receipt/line_item/store/extraction_review looks like. This is `vela-core`'s JSON Schema (`*.schema.json`), unchanged, and is the single source of truth. `vela-etl` generates its own Python types from these files via `datamodel-code-generator`, independent of anything on `vela-api`'s side.
2. **Endpoint shape** — URL, method, and request/response wrapper. Described via an OpenAPI spec whose request/response bodies wrap or reference `vela-core`'s JSON Schema directly, rather than redefining shapes.

No static type generation is needed from the OpenAPI spec itself. It only ever describes the endpoint wrapper around data whose types already come from JSON Schema. Load reads the spec as documentation, to learn what endpoint to call. It then issues a plain HTTP call. This call uses the types already generated from JSON Schema.

`vela-etl` only needs to satisfy this contract: data shape and endpoint description. It has no need to know or mirror any tooling choice made on `vela-api`'s side, regardless of what language or code-generation approach `vela-api` uses internally.

### Write contract shape (finalized)

`vela-api` exposes a **single combined payload endpoint**: one POST carrying the whole shaped receipt, its line items, and any extraction_reviews. The request body nests all three and writes them atomically.

This is _not_ per-table endpoints (`POST /receipts`, `POST /line-items`, `POST /extraction-reviews`). Rationale:

- Matches `vela-etl`'s natural output shape. Emit already bundles receipt, line_items, and reviews into one unit, so a combined endpoint means no re-splitting on the way out.
- Avoids partial-write states (e.g. receipt succeeds, line_items call fails, review call never happens).
- Fewer round trips per receipt.

This is `vela-etl`'s stated design expectation, handed off as a requirement to whoever implements `vela-api`. It is not a guarantee of `vela-api`'s internals, which remain out of this repo's control.

### Schema validation responsibility (finalized)

**Both sides validate** against `vela-core`'s JSON Schema:

- `vela-etl` validates and shapes data before sending, using the `datamodel-code-generator`-generated types. This fails fast with good errors during development, before a request ever leaves the pipeline.
- `vela-api` re-validates on receipt as defense in depth, since it can't fully trust every caller, especially once other clients might exist.

**Invariant this protects:** the database's integrity never depends on `vela-etl`'s validation being correct or current. `vela-etl`'s generated types might drift from the real schema. A future caller might skip validation entirely. Either way, `vela-api`'s own re-validation is what actually keeps bad data out. `vela-etl`'s check is an optimization — fail fast, good dev-time errors — never the enforcement mechanism.

---

## Extraction

- Extractors are a **list**, not a special-cased "one vs. many" branch. Config decides how many run. The same loop handles 1 or N.
- All extractor kinds (OCR, vision LLM, manual) implement one common interface:

  ```txt
  extract(image) -> ExtractionResult
  ExtractionResult = { source, candidate: {store, receipt, line_items}, confidence: {field_name: score} }
  ```

- No extractor `kind` field — `source`/`name` plus which config list an extractor sits in is enough.
- **Confidence has three possible origins:**
  1. Self-reported by the model (if prompted for it) — soft signal, can be overconfident.
  2. Native to the tool (OCR engines often give real per-word/char confidence).
  3. Derived by us via validation (e.g. does `quantity × unit_price == line_total`) — always available, used as a fallback since `confidence_score` is a required (non-null) column in `extraction_reviews`.
- **Fallback extractors are not a special case.** Extractor A may be low-confidence, causing extractor B to run. That is just a second `ExtractionResult` appended to the same results list. Reconciliation doesn't know or care _why_ a second result exists. It only knows that it does. "Why B ran" is an orchestration-time decision that isn't persisted anywhere.
- **We don't care what happens inside the extraction/orchestration process. We only care that input and output are predictable and deterministic.** An extractor may be OCR, a vision LLM, or a manual queue. One or several may have run. A fallback may have fired because of a confidence threshold. None of that is anyone else's concern downstream. Extractors are plug-in units: as long as one satisfies the `extract(image) -> ExtractionResult` interface, it can be swapped in or out without touching orchestration, reconciliation, or anything downstream.
- **The contract that matters:** given an image and whatever config decided to run, you get back a results list. It has the same shape every time. One entry flows straight through. Two or more entries get checked for agreement. Everything after extraction is built against that one predictable shape. It is never built against the internal process that produced it.
- Total extraction failure (unreadable image, API error) → the extractor returns an `extraction_reviews`-shaped row directly (e.g. `flagged_reason: "illegible"`), since there's no candidate to reconcile.

### Extractor modules

Each implements `extract(image) -> ExtractionResult`, or returns an `extraction_reviews`-shaped row on total failure. Each also slots into the extractor list as a plain function. Config decides which run for a given receipt. The orchestrator never branches on kind. **This is the payoff of the plug-in interface above.** Adding a new vendor — a 10th row to the table below — is purely additive. Write one adapter satisfying the interface, then add it to config. This requires zero changes to orchestration, reconciliation, or any other stage.

| Item                                           | Loadable as an extractor module? | What the adapter has to do                                                                                                      |
| ---------------------------------------------- | -------------------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| `pytesseract`                                  | Yes                              | Wrap raw text output, parse into store/receipt/line_items shape yourself                                                        |
| EasyOCR                                        | Yes                              | Same — raw text/boxes out, own mapping logic                                                                                    |
| PaddleOCR                                      | Yes                              | Same — general OCR, needs own mapping logic                                                                                     |
| docTR                                          | Yes                              | Same — general OCR, needs own mapping logic                                                                                     |
| AWS Textract (AnalyzeExpense)                  | Yes                              | Response is already receipt-shaped — adapter mostly renames fields, needs AWS credentials                                       |
| Azure Document Intelligence (prebuilt receipt) | Yes                              | Same as Textract — receipt-shaped output, needs Azure credentials                                                               |
| Claude / GPT-4V / Gemini                       | Yes                              | Prompted to return JSON matching schema directly — adapter mostly parses that JSON, needs API key                               |
| Qwen2.5-VL / Llama Vision via Ollama           | Yes                              | Same pattern as vision LLMs, called over Ollama's local HTTP API instead of a paid vendor                                       |
| Manual                                         | Not a module                     | A person filling a form in `vela-api`'s dashboard — no code, but returns the same `ExtractionResult`/`extraction_reviews` shape |

Cloud vendors with receipt-specific parsing (Textract, Azure) need the least adapter work, since their output is already close to the schema. General-purpose OCR needs the most, since raw text must be turned into structured fields by hand. Vision LLMs sit in between. The prompt steers the output shape, but the response still needs parsing and validation.

---

## Transform pipeline

Order: `reconcile → validate → shape → emit`

### 1. Reconcile

- Per field, per receipt: compare all extractor values.
- `len(distinct values) == 1` → use the value directly (covers both "only one extractor ran" and "multiple extractors agreed" — same code path).
- Otherwise → one review row per disagreeing extractor, all sharing `receipt_id` + `field_name` (+ `line_item_id` if relevant), `flagged_reason: "conflicting_extractions"`, `status: "pending"`.
- Always returns a list, length 1 or N. **Invariant this protects:** reconciliation never knows or asks how many extractors ran, or why a given one produced a result. It only ever looks at the values in the list it was handed. Adding a third extractor, or changing when a fallback fires, requires zero changes here.

### 2. Validate

- Runs _after_ reconcile, only on resolved values.
- Checks internal consistency: `quantity × unit_price == line_total`, line items sum to total/subtotal, required fields non-null.
- Produces more review rows, same shape as reconciliation output, `flagged_reason: "validation_failed"`.

### 3. Shape

- Split fields into known schema columns vs. `extras` (JSONB catch-all for rare/store-specific fields).
- Assign `line_order` (1-indexed) to line items.
- Compute `content_hash` from `store_id + transaction_ref + date + total`.

### 4. Emit

- Combine reconciliation reviews + validation reviews into one list.
- Output: shaped receipt record, line items, full review-row list — ready for Load.

### extraction_reviews row rules

- Single wrong field → `field_name` = real column name (+ `line_item_id` if line-item-level).
- Whole line item wrong (merged/split/invented) → `field_name = "line_item"` sentinel.
- Missed item entirely → `field_name = "missing_line_item"` sentinel, no `line_item_id`.
- Multiple extractors disagreeing on the same field → one row per extractor, same `receipt_id`/`line_item_id`/`field_name`, `flagged_reason = "conflicting_extractions"`. Agreement produces zero rows.
- `flagged_reason` (why the row exists) and `status` (`pending`/`resolved`/`rejected`) are independent — `rejected` is reversible, not terminal.

---

## Privacy and anonymization (finalized)

Privacy stance is **data minimization, not redaction**: extract only what `vela-core`'s schema defines, in required, common, and rare tiers, with rare fields going into `extras`. There is nothing extra to redact afterward.

Per-field review:

- `stores.*` — business info, not personal.
- `receipts.staff_name` — a real person's name, first-name-only, and the store already discloses it to every customer by printing it on the receipt. Not meaningfully private in this context.
- `receipts.customer_name` and `extras` — **confirmed**: this is a personal, local project processing only the project owner's own receipts, with no third-party exposure path. Neither field ever contains someone else's personal data in this context.

**Decision: anonymization/redaction is not needed.** The team mitigates the platform repo being public separately, via synthetic, faker-generated seed data for anything public. Real extracted data stays local only.

**Revisit if:** the dashboard (`vela-api`) or `vela-agent` is ever hosted somewhere reachable by anyone other than the project owner.

**If reversible redaction is ever reinstated:** field-level (not whole-row) encryption is the preferred approach. The key stays separate from the data. Decryption is a permissioned, logged action, not automatic on read.

**Follow-up outside this repo's scope:** `vela`'s master-plan doc currently states redaction as a fact: "`vela-etl` redacts personal details... before data reaches storage." Someone needs to correct this to match the decision above. It is not `vela-etl`'s file to fix, but this note flags it so it doesn't get missed.

---

## Load

- `vela-etl` calls `vela-api` over HTTP to write the shaped receipt, line items, and extraction_reviews in one combined-payload request — no DB driver, no DB credentials.
- **Duplicate check via `content_hash`**, a unique index on `receipts` in the real schema, is enforced entirely on `vela-api`'s side. `vela-etl` only _computes_ the hash, a pure function of `store_id + transaction_ref + date + total` done during Shape. It has no DB connection and no way to check whether that hash already exists. `vela-etl` sends the request and handles whatever comes back: success, or an "already exists" response. **Invariant this protects:** `vela-etl` structurally cannot become a second, possibly-stale source of truth for what's already stored. The only thing capable of answering "does this already exist" is the one system that can actually see the data, by design, not by convention.

---

## Tool inventory (Python, all stages)

| Stage             | Purpose                                  | Tool                                                                           | Pricing                            |
| ----------------- | ---------------------------------------- | ------------------------------------------------------------------------------ | ---------------------------------- |
| Extract           | OCR, free/local                          | `pytesseract`, EasyOCR, PaddleOCR, or docTR                                    | Free, open source                  |
| Extract           | OCR, cloud vendor                        | AWS Textract (AnalyzeExpense) / Azure Document Intelligence (prebuilt receipt) | Paid, both have limited free tiers |
| Extract           | Vision LLM, cloud                        | Claude / GPT-4V / Gemini SDKs                                                  | Paid, pay-per-use                  |
| Extract           | Vision LLM, self-hosted                  | Qwen2.5-VL or Llama 3.2 Vision via Ollama                                      | Free, open source (self-hosted)    |
| Extract           | Image preprocessing                      | OpenCV                                                                         | Free, open source                  |
| Extract           | Retry/backoff on flaky vendor calls      | `tenacity`                                                                     | Free, open source                  |
| Extract/Transform | Validate data shapes                     | `pydantic`                                                                     | Free, open source                  |
| Transform         | Validate against vela-core's JSON Schema | `jsonschema` or `pydantic`                                                     | Free, open source                  |
| Transform         | Generate Python types from JSON Schema   | `datamodel-code-generator`                                                     | Free, open source                  |
| Transform         | Content hash for dedup                   | `hashlib` (built-in)                                                           | Free                               |
| Load              | HTTP calls to vela-api                   | `httpx`                                                                        | Free, open source                  |
| Load              | Mock vela-api in tests                   | `respx`                                                                        | Free, open source                  |
| All               | Testing                                  | `pytest`                                                                       | Free, open source                  |
| All               | Logging                                  | `structlog` or built-in `logging`                                              | Free, open source                  |

---

## Remaining open items

Genuinely outside `vela-etl`'s control — depend on decisions made when `vela-api` is actually built:

- Whether `vela-api` formalizes its endpoint contract as an OpenAPI spec at all, or documents it some other way. `vela-etl` has stated its expectation above: a combined payload endpoint, OpenAPI-described. The actual implementation is `vela-api`'s call.
