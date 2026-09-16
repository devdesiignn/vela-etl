# Design Notes

Working notes from design discussion, covering Extract → Transform → Load. Intended as context for implementation (language: TypeScript).

---

## Architecture context

**This section supersedes an earlier assumption.** Originally assumed `vela-etl` connects directly to Postgres, since `vela-core` is just schema/migrations, not a running service. The `vela-etl` README has since been updated and states the opposite:

- `vela-etl` does **not** connect to `vela-core`'s database directly.
- `vela-etl` writes structured data **and review resolutions** via `vela-api` — i.e. `vela-api` is not read-only after all; it exposes write endpoints too (create receipt/line_items/extraction_reviews, resolve a review).
- This resolves the earlier open gap about where manual review resolution lives: it's `vela-api`'s job (it has the dashboard + now the write path), not `vela-etl`'s.
- Consequence for `vela-etl`: no DB credentials, no Postgres driver/query builder needed. It needs an HTTP client for `vela-api` instead.
- New explicit scope constraint (from the updated README): `vela-etl` **only extracts fields defined in `vela-core`'s schema — nothing beyond that is captured or retained.**
- **Decision: contract between `vela-etl` and `vela-api` will be an OpenAPI spec.** Since these are genuinely separate services now, something has to define request/response shapes both sides agree on — generated TS types on the `vela-etl` side, generated docs, and a natural pairing with the JSON Schema files already in `vela-core` (request bodies would wrap/reference `receipt.schema.json` etc. rather than redefining shapes twice). `vela-api` isn't built yet (weeks 5–7); sketching the OpenAPI spec early lets `vela-etl`'s Load step build against a real contract instead of guessed shapes.

**Still open:**

- What `vela-api`'s write contract actually looks like — per-table REST endpoints (`POST /receipts`, `POST /line-items`, `POST /extraction-reviews`) vs. one combined endpoint taking a whole shaped receipt+line_items+reviews payload.
- Who validates against `vela-core`'s JSON schemas — `vela-etl` before sending, `vela-api` on receipt, or (most likely) both, with `vela-etl` shaping correctly and `vela-api` re-validating since it can't fully trust every caller.
- Where the `content_hash` duplicate check now lives — this used to be `vela-etl`'s job via Postgres's unique index; now it belongs to whoever implements `vela-api`'s write endpoint. `vela-etl` just calls the API and handles whatever response comes back (success, or "already exists").

---

## Extraction

- Extractors are a **list**, not a special-cased "one vs many" branch. Config decides how many run; the same loop handles 1 or N.
- All extractor kinds (OCR, vision LLM, manual) implement one common interface:

  ```txt
  extract(image) -> ExtractionResult
  ExtractionResult = { source, candidate: {store, receipt, line_items}, confidence: {field_name: score} }
  ```

- Dropped the idea of an extractor `kind` field — unnecessary; `source`/`name` plus which config list an extractor sits in is enough.
- **Confidence has three possible origins**, not just one:
  1. Self-reported by the model (if prompted for it) — soft signal, can be overconfident.
  2. Native to the tool (OCR engines often give real per-word/char confidence).
  3. Derived by us via validation (e.g. does `quantity × unit_price == line_total`) — always available, used as a fallback since `confidence_score` is a required (non-null) column in `extraction_reviews`.
- **Fallback extractors are not a special case.** If extractor A is low-confidence and extractor B is called as a result, that's just a second `ExtractionResult` appended to the same results list. Reconciliation doesn't know or care _why_ a second result exists — only that it does. "Why B ran" is an orchestration-time decision that isn't persisted anywhere. Regardless of whether config always runs both extractors, or runs one and conditionally falls back to a second, the caller ends up with the same shape: a list of N results for one receipt. Reconciliation logic never has to branch on _how many ran or why_ — 1 entry flows straight through, 2+ entries get checked for agreement.
- **We don't care what happens inside the extraction/orchestration process — only that input and output are predictable and deterministic.** Whether an extractor is OCR, a vision LLM, a manual queue, whether one or several ran, whether a fallback fired because of a confidence threshold — none of that is anyone else's concern downstream. The contract that matters is: _given an image (and whatever config decided to run), you get back a results list in the same shape every time_ (`{source, candidate, confidence}` per entry, or an `extraction_reviews`-shaped row on total failure). Everything after extraction (reconcile, validate, shape, emit) is built against that one predictable shape, never against the internal process that produced it.
- Total extraction failure (unreadable image, API error) → extractor returns an `extraction_reviews`-shaped row directly (e.g. `flagged_reason: "illegible"`), since there's no candidate to reconcile.

## Transform — order matters, established as

```txt
reconcile → validate → shape → emit
```

(anonymize step removed — see below)

### 1. Reconcile

- Per field, per receipt: compare all extractor values.
- `len(distinct values) == 1` → use the value directly (this covers both "only one extractor ran" and "multiple extractors agreed" — same code path, no special case).
- Otherwise → one review row per disagreeing extractor, all sharing `receipt_id` + `field_name` (+ `line_item_id` if relevant), `flagged_reason: "conflicting_extractions"`, `status: "pending"`.
- Always returns a list (length 1 or N) — consistent shape regardless of outcome.

### 2. Validate

- Runs _after_ reconcile, only on resolved values (can't validate something still stuck in review).
- Checks internal consistency: `quantity × unit_price == line_total`, line items sum to total/subtotal, required fields non-null.
- Produces more review rows, same shape as reconciliation output, `flagged_reason: "validation_failed"`.

### 3. Shape

- Split fields into known schema columns vs. `extras` (JSONB catch-all for rare/store-specific fields).
- Assign `line_order` (1-indexed) to line items — DB rows have no inherent order.
- Compute `content_hash` from `store_id + transaction_ref + date + total`, used for dedup.

### 4. Emit

- Combine reconciliation reviews + validation reviews into one list.
- Output: shaped receipt record, line items, full review-row list — ready for Load.

### Anonymization — considered, then dropped

- Originally considered because the _platform repo_ would be public. Real mitigation for that is already in place: **synthetic/faker-generated seed data** for anything public; real extracted data stays local only.
- Reviewed what's actually sensitive in the schema:
  - `stores.*` — business info, not personal.
  - `receipts.staff_name` — a real person's name, but first-name-only, and the store already discloses it to every customer by printing it on the receipt. Not meaningfully private in this context.
  - `receipts.customer_name` — need to confirm whether this is ever someone other than the person running the project (only matters if so).
  - `extras` (JSONB) — flagged as the one place worth double-checking for something like a partial card number, if any sample receipts contain one.
- Given: purely local setup, no hosted dashboard currently planned, no real third-party exposure path — **decision: anonymization is not needed for now.**
- Revisit if: the dashboard (`vela-api`) or `vela-agent` is ever hosted somewhere reachable by anyone other than the project owner.
- **Action item:** `vela`'s master-plan doc currently states redaction as a fact ("`vela-etl` redacts personal details... before data reaches storage") — needs updating to match this decision.
- If reversible redaction is ever reinstated: field-level (not whole-row) encryption was the preferred approach, with the key held separately from the data and decryption treated as a permissioned, logged action — not automatic on read.

## Load (revised — writes now go through `vela-api`, not direct Postgres)

- `vela-etl` calls `vela-api` over HTTP to write the shaped receipt, line items, and extraction_reviews — no DB driver, no DB credentials on the `vela-etl` side.
- **Duplicate check via `content_hash`** (unique index on `receipts` in the actual schema, to catch cases like a single physical receipt photographed twice) is now enforced on `vela-api`'s side. `vela-etl` only _computes_ the hash (a pure function of `store_id + transaction_ref + date + total`, done during Shape) — it has no way to check whether that hash already exists, since it no longer holds a DB connection at all. The actual accept/reject decision belongs entirely to `vela-api`, since it's the only thing that can see what's already stored. `vela-etl` just sends the request and handles whatever comes back — success, or an "already exists" response.
- Contract for these calls is being defined via an **OpenAPI spec**, shared between `vela-etl` and `vela-api`, so both sides build against the same agreed shapes rather than guessing.
- Still open: per-table endpoints vs. one combined payload endpoint (see Architecture context above); who validates against `vela-core`'s JSON schemas (likely both sides).

## Language

- Chosen: **TypeScript** (over a "systems language," which isn't needed — this pipeline is I/O-bound: API calls out to OCR/vision vendors, JSON in/out, Postgres writes; nothing CPU-bound enough to require a systems language).
- Advantages specific to this project: schemas are JSON Schema, so TS types can be generated directly from `store.schema.json` / `receipt.schema.json` / `line_item.schema.json` / `extraction_review.schema.json` rather than drifting from the real contract. Mature Postgres drivers (`pg`, or typed query builders like Kysely/Drizzle). First-class SDKs for major OCR/vision vendors.

## Still open / unresolved

1. ~~Where does manual review resolution live~~ — **resolved**: `vela-api`, via its write endpoints. `vela-etl` never touches review resolution.
2. `vela-api`'s write contract shape — per-table endpoints vs. one combined payload endpoint. Needs the OpenAPI spec sketched out.
3. Who validates against `vela-core`'s JSON schemas — likely both `vela-etl` (shaping correctly before sending) and `vela-api` (re-validating, since it can't fully trust every caller).
4. Confirm whether `receipts.customer_name` or anything in `extras` is ever someone else's sensitive data, now that anonymization has been dropped.
