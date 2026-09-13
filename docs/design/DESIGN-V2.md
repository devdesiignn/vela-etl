# Design Notes v2

Supersedes the language/architecture sections of the original design notes. Extract → Transform → Load pipeline logic and the Architecture context (Load calls receipt-api, not Postgres directly) carry over unchanged from v1. This version resolves what was previously an open "TypeScript" assumption.

---

## Language: Python (whole pipeline)

**Decision:** `receipt-etl` — Extract, Transform, and Load — runs as a single Python codebase. No subprocess calls, no HTTP hops, no cross-language runtime dependency between the three stages.

**Why Python, not TypeScript:**

- Extract carries the bulk of the pipeline's real complexity and compute cost (OCR processing, vision-LLM calls, confidence scoring, fallback logic, per-vendor adapters). Python's OCR/ML ecosystem is native and mature here: EasyOCR, PaddleOCR, docTR, `pytesseract` all run in-process with no bridge needed. The equivalent tools in TypeScript are either thin (Tesseract.js) or nonexistent, and would require calling out to a separate Python service anyway.
- Transform (reconcile → validate → shape → emit) and Load (call `receipt-api`) are both thin, language-neutral steps — plain logic and a handful of HTTP calls. Neither has any real technical pull toward TypeScript. Since Extract already needs Python, keeping all three stages in one language avoids introducing a cross-language boundary purely for stages that don't need one.
- Node/TypeScript is not a runtime dependency of `receipt-etl`. It plays no role anywhere in the running pipeline.

**What this replaces:** the original design notes stated a TypeScript choice as settled ("Language: Chosen: TypeScript"). That was a draft assumption, not a final decision — this doc corrects it.

---

## Contract with receipt-api

Two separate things, not one:

1. **Data shape** — what a receipt / line_item / store / extraction_review looks like. This is `receipt-core`'s JSON Schema (`*.schema.json`), unchanged, and remains the single source of truth. `receipt-etl` generates its own Python types from these files using `datamodel-code-generator`, independent of anything on `receipt-api`'s side.
2. **Endpoint shape** — what URL, method, and request/response wrapper `receipt-api` exposes for writing data. If/when `receipt-api` documents this as an OpenAPI spec, that spec's request/response bodies wrap or reference `receipt-core`'s JSON Schema directly rather than redefining shapes.

**No static type generation from the OpenAPI spec is needed.** The data types already come from `receipt-core`'s JSON Schema (point 1, above) — OpenAPI in this setup only ever describes the endpoint/method wrapper around that same data, so there's nothing left for a second code-generation step to produce. Load reads the spec (as documentation, once `receipt-api` exists) to know what endpoint to call and writes a plain HTTP call against it, using the types it already generated from the JSON Schema.

This reflects the actual meaning of "independent repos following a contract": `receipt-etl` only needs to satisfy the contract (data shape + endpoint description). It has no need to know or mirror any tooling choice made on `receipt-api`'s side, regardless of what language or code-generation approach `receipt-api` itself uses internally.

---

## Still open (unchanged from v1, deferred to receipt-api's build phase)

- Per-table endpoints vs. one combined payload endpoint on `receipt-api`.
- Whether `receipt-api` formalizes its endpoint contract as an OpenAPI spec at all, or documents it some other way — not decided, not `receipt-etl`'s concern to decide.
- Who validates against `receipt-core`'s JSON Schema on each side — likely both, `receipt-etl` shaping correctly before sending, `receipt-api` re-validating on receipt.
- Confirm whether `receipts.customer_name` or anything in `extras` is ever someone else's sensitive data.

---

## Extractor modules

Each of these implements the shared `extract(image) -> ExtractionResult` interface (or returns an `extraction_reviews`-shaped row on total failure) and slots into the extractor list as a plain function. Config decides which run for a given receipt; the orchestrator loop never branches on kind.

| Item                                           | Loadable as an extractor module? | What the adapter has to do                                                                                                                                                                                               |
| ---------------------------------------------- | -------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `pytesseract`                                  | Yes                              | Wrap its raw text output, parse into store/receipt/line_items shape yourself (it doesn't understand receipts, just text)                                                                                                 |
| EasyOCR                                        | Yes                              | Same as above — raw text/boxes out, your code has to map it into the candidate shape                                                                                                                                     |
| PaddleOCR                                      | Yes                              | Same — general OCR, needs your own mapping logic                                                                                                                                                                         |
| docTR                                          | Yes                              | Same — general OCR, needs your own mapping logic                                                                                                                                                                         |
| AWS Textract (AnalyzeExpense)                  | Yes                              | Its response is already receipt-shaped (store, line items, totals) — adapter is mostly renaming fields to match your schema, plus needs AWS credentials                                                                  |
| Azure Document Intelligence (prebuilt receipt) | Yes                              | Same as Textract — receipt-shaped output already, needs Azure credentials                                                                                                                                                |
| Claude / GPT-4V / Gemini                       | Yes                              | Prompted to return JSON matching your schema directly — adapter is mostly parsing that JSON, plus needs API key/credentials                                                                                              |
| Qwen2.5-VL / Llama Vision via Ollama           | Yes                              | Same pattern as above, called over Ollama's local HTTP API instead of a paid vendor's                                                                                                                                    |
| Manual                                         | Not a module                     | A person filling a form in `receipt-api`'s dashboard — no code to load, but still returns the same `ExtractionResult`/`extraction_reviews` shape so the rest of the pipeline treats it identically to the automated ones |

Cloud vendors with receipt-specific parsing (Textract, Azure) need the least adapter work since their output is already close to your schema. General-purpose OCR (Tesseract, EasyOCR, PaddleOCR, docTR) needs the most, since raw text has to be turned into structured fields by hand. Vision LLMs sit in between — the output shape is steered via the prompt, but the response still has to be parsed and validated.

---

## Tool inventory (Python, all stages)

| Stage             | Purpose                                     | Tool                                                                           | Pricing                            |
| ----------------- | ------------------------------------------- | ------------------------------------------------------------------------------ | ---------------------------------- |
| Extract           | OCR, free/local                             | `pytesseract`, EasyOCR, PaddleOCR, or docTR                                    | Free, open source                  |
| Extract           | OCR, cloud vendor                           | AWS Textract (AnalyzeExpense) / Azure Document Intelligence (prebuilt receipt) | Paid, both have limited free tiers |
| Extract           | Vision LLM, cloud                           | Claude / GPT-4V / Gemini SDKs                                                  | Paid, pay-per-use                  |
| Extract           | Vision LLM, self-hosted                     | Qwen2.5-VL or Llama 3.2 Vision via Ollama                                      | Free, open source (self-hosted)    |
| Extract           | Image preprocessing                         | OpenCV                                                                         | Free, open source                  |
| Extract           | Retry/backoff on flaky vendor calls         | `tenacity`                                                                     | Free, open source                  |
| Extract/Transform | Validate data shapes                        | `pydantic`                                                                     | Free, open source                  |
| Transform         | Validate against receipt-core's JSON Schema | `jsonschema` or `pydantic`                                                     | Free, open source                  |
| Transform         | Generate Python types from JSON Schema      | `datamodel-code-generator`                                                     | Free, open source                  |
| Transform         | Content hash for dedup                      | `hashlib` (built-in)                                                           | Free                               |
| Load              | HTTP calls to receipt-api                   | `httpx`                                                                        | Free, open source                  |
| Load              | Mock receipt-api in tests                   | `respx`                                                                        | Free, open source                  |
| All               | Testing                                     | `pytest`                                                                       | Free, open source                  |
| All               | Logging                                     | `structlog` or built-in `logging`                                              | Free, open source                  |
