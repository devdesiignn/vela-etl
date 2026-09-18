# Extract, Transform, and Load, explained for a new Python dev

This walks through the four completed phases in `vela-etl` — **Phase 1**
(repo and schema foundation), **Extract**, **Transform**, and **Load** —
plus the shared type layer that Phase 1 produces and that all three later
stages depend on. It assumes you know JavaScript/TypeScript but are new to
Python, and it leans on real-life analogies wherever the code's *purpose*
isn't obvious from its syntax.

Scope: this covers what's actually built and tested today — the project
scaffolding and tooling, `src/etl/types/`, `src/etl/extract/`,
`src/etl/transform/`, `src/etl/load/`, and `mock_api/`. Extract's *real*
adapters (actual OCR/vision-LLM vendors turning a photo into raw guesses)
are **not** covered here because they aren't implemented yet — only the
interface and orchestrator that will run them are. See
`docs/IMPLEMENTATION-PLAN.md` for current phase status.

---

## 1. The big picture: what problem is this solving?

Imagine you hand a stack of paper receipts to three different interns and ask
each of them to type up what's on each receipt into a spreadsheet. You will get
three spreadsheets back. They will mostly agree. Sometimes they won't — one
intern misreads a price, another skips a line item, a third can't read the
store's handwriting at all.

`vela-etl`'s job is to take those three (or one, or five) independent guesses
at "what does this receipt say," and turn them into:

1. **One trusted final answer** for everything the guesses agree on, or where
   only one guess exists.
2. **A clearly labeled list of "someone needs to look at this"** for
   everything they disagreed on, or that fails a sanity check (like the math
   not adding up).

That's it. That's the whole job of Transform. Load is simpler: once you have
that trusted final answer, Load's job is just "mail it to the office that
keeps the real filing cabinet" — in this case, a future service called
`vela-api`.

**Real-life analogy:** think of Transform as the *editor* of a newspaper who
receives three reporters' drafts on the same story. If all three say the fire
started at 3pm, the editor prints "3pm" with confidence. If they disagree on
the number of injuries, the editor doesn't guess — they flag it for a
fact-checker to resolve before publication. Load is the delivery truck that
takes the finished, approved page to the printing press. The editor never
drives the truck, and the truck never rewrites the page.

---

## 2. Phase 1 — repo and schema foundation

Before any pipeline logic could be written, the project needed its
scaffolding: a build system, a way to stay in sync with the schema owned by
a *different* repo, tests that prove the generated types are trustworthy,
and automated checks that catch mistakes before they ever reach a teammate.
None of this produces business logic — no reconciling, no validating,
nothing that touches a receipt. Everything else builds on this foundation,
which is exactly why it had to come first.

**Real-life analogy:** Phase 1 is pouring the concrete foundation and
running the electrical wiring before anyone starts framing rooms. You can't
see it in the finished house, but every wall built afterward depends on it
being level and correctly wired.

### 2.1 `uv` and `pyproject.toml` — the toolbox

`vela-etl` uses [`uv`](https://docs.astral.sh/uv/) to manage Python
dependencies and virtual environments, with a lockfile (`uv.lock`) pinning
exact versions.

**JS/TS analogy:** `uv` plays the same role as `npm`/`pnpm`/`yarn` —
`pyproject.toml` is `package.json`, `uv.lock` is `package-lock.json`. `uv
sync` installs exactly what's in the lockfile, the same guarantee
`npm ci` gives you.

Python's package ecosystem has no built-in equivalent of npm's `"scripts"`
field, so this repo adds one via a task runner called
[`poethepoet`](https://github.com/nat-n/poethepoet), configured entirely
inside `pyproject.toml`'s `[tool.poe.tasks]` table and invoked as
`uv run poe <task>`:

```toml
[tool.poe.tasks]
bootstrap = ["submodule:init", "gen:types"]
test = "pytest"
pretest = "bootstrap"
lint = "ruff check ."
format = "ruff format ."
```

**JS/TS analogy:**

```json
// package.json
"scripts": {
  "bootstrap": "npm run submodule:init && npm run gen:types",
  "pretest": "npm run bootstrap",
  "test": "jest",
  "lint": "eslint .",
  "format": "prettier --write ."
}
```

`pretest` running automatically before `test` is the same convention
`npm test`'s implicit `pretest` hook gives you for free — it's why
`uv run poe test` works from a completely fresh clone with zero manual setup
steps: it bootstraps itself first.

### 2.2 The submodule — staying in sync with someone else's schema

`vela-etl` doesn't own the definition of what a `Receipt` or `LineItem`
looks like. A sibling repo, `vela-core`, does. So this repo vendors
`vela-core` as a **git submodule**, pinned to one specific commit, living
at `vendor/vela-core`.

**JS/TS analogy:** picture publishing your design tokens or schema package
to a private npm registry and another team installing it at an exact pinned
version (`"@company/schema": "1.4.2"`, not `"^1.4.0"`) — except instead of a
package registry, it's git itself doing the pinning, via a submodule commit
SHA rather than a semver range.

**Real-life analogy:** it's like a construction company keeping a *physical
copy* of the government's official building-code manual on-site, dated and
stamped with exactly which edition they're building to — instead of trusting
someone's verbal summary of "roughly what the code says today." If the code
changes next year, they don't automatically start building to it. They
deliberately pull the new edition, review what changed, and update every
blueprint that referenced the old one.

Two commands manage this relationship:

- `uv run poe submodule:init` — first-time setup, pulls `vela-core` at its
  currently-pinned commit. Nothing changes. You're just pulling what's
  already agreed on.
- `uv run poe submodule:update` — a *deliberate* decision to move the pin
  forward to a newer `vela-core` commit, then stage that change for review.

This project's own `CLAUDE.md` treats this so seriously that it's a standing
rule: check for schema drift at the start of *every* session, not just once.

### 2.3 Generating types from the schema

Once the submodule holds the vendored schema, `uv run poe gen:types` runs
`datamodel-codegen` against `vendor/vela-core/schemas/*.schema.json` and
writes Python classes into `src/etl/types/` — the `Store`, `Receipt`,
`LineItem`, and `ExtractionReview` models covered in §3 below.

**JS/TS analogy:** this is `openapi-typescript` or `json-schema-to-zod`
pointed at someone else's schema file, run as a build step rather than
hand-typed. The output files literally start with a
`# generated by datamodel-codegen` header comment, the same signal as a
`*.generated.ts` filename convention — a flag that says "don't hand-edit
this, it'll be overwritten."

A round-trip test (`tests/test_generated_types.py`) proves the generated
types actually work as validators: constructing a `Store`/`Receipt` (with a
nested `LineItem`) from valid data succeeds, and doing the same with a
*missing required field* raises an error. This is the safety net that
catches "the schema changed in a way that broke our assumptions" the moment
it happens, rather than three files deep into Transform logic.

### 2.4 Automated guardrails — `pre-commit` and CI

Two layers catch mistakes at different points in the workflow:

**`pre-commit`** runs `ruff check --fix` and `ruff format` automatically on
every `git commit`, rewriting bad code before it's even committed.

**JS/TS analogy:** this is exactly `husky` + `lint-staged` running `eslint
--fix` and `prettier --write` on your staged files before Git lets a commit
through — same idea, same trigger point, Python-native tools instead. (You
actually saw this fire in this very conversation: this exact hook
auto-reformatted the commit that added this doc's earlier version before
Git let it land.)

**GitHub Actions CI** (`.github/workflows/ci.yml`) runs `uv run poe lint`
and `uv run poe test` on every push and pull request, including checking out
the `vela-core` submodule so the generated-types round-trip test can
actually run in a clean environment — the same role a `.github/workflows/*`
file plays in any JS/TS repo running `eslint` and `jest` on every PR.

**Real-life analogy:** `pre-commit` is a spell-checker that fixes typos
*while you're still writing the letter*, before you seal the envelope. CI is
the editor at the printing press who checks the final page one more time
*after* you submit it, as a second independent safety net — catching
anything that slipped through, or anything that only breaks once combined
with someone else's changes.

### 2.5 Why Phase 1 had to come first

The implementation plan states this plainly: Phase 1 **blocks everything
else**, because Extract, Transform, and Load all construct and depend on the
generated types from `src/etl/types/`. There's no version of Transform's
`shape()` function (§5.3 in the Transform section below) that makes sense
without a `Receipt` class to construct — and there's no `Receipt` class
without Phase 1's submodule pin and code generation having already run.

---

## 3. The shared vocabulary: `src/etl/types/`

Before either Transform or Load can do anything, everyone needs to agree on
*what a receipt record looks like*. That's `src/etl/types/`.

These files are not hand-written. They're **generated** from a JSON Schema
that lives in a sibling project, `vela-core` (vendored into this repo as a
pinned git submodule at `vendor/vela-core`). The command
`uv run poe gen:types` runs a tool called `datamodel-codegen` that reads
`vendor/vela-core/schemas/*.schema.json` and spits out Python classes.

**JS/TS analogy:** this is exactly like running `openapi-typescript` or
`json-schema-to-zod` against someone else's OpenAPI spec instead of typing
`interface Receipt { ... }` by hand. If the upstream schema changes, you
regenerate — you never hand-edit the generated file, the same way you'd never
hand-edit `api-types.generated.ts`.

**Real-life analogy:** it's like a bank and a merchant both agreeing to use
the exact same receipt printer template, so a receipt from one always parses
the same way at the other. Nobody improvises the format.

The four generated types, and what each represents:

### `Store`

```python
class Store(BaseModel):
    id: UUID
    name: str
    address: str
    phone: str | None = None
    email: str | None = None
    website: str | None = None
    created_at: AwareDatetime | None = None
```

A physical shop. `name` + `address` together are meant to be unique — two
branches of the same chain (e.g. two Starbucks at different addresses) are
two different `Store` rows, not one.

### `Receipt`

```python
class Receipt(BaseModel):
    id: UUID
    store_id: UUID
    source_image_id: str
    transaction_ref: str
    date: date
    total: float
    content_hash: str  # <- important, explained below
    extras: dict[str, Any] | None = None
    line_items: list[LineItem] | None = None
    ...
```

One shopping trip's receipt. `content_hash` is a fingerprint (explained in
the Shape step below) used to detect "you already uploaded a photo of this exact receipt."
`extras` is a catch-all bucket for anything real but not important enough to
be its own database column — like a loyalty-points footer, or a "thank you"
message. Nothing captured there is lost, it's just not first-class.

### `LineItem`

```python
class LineItem(BaseModel):
    id: UUID
    receipt_id: UUID
    description: str
    quantity: float
    unit_price: float
    line_total: float
    line_order: int
```

One row on the receipt: "Milk x2 @ $3.50 = $7.00." `line_order` is its
1-indexed position on the printed receipt (item 1, item 2, item 3...).

### `ExtractionReview`

```python
class ExtractionReview(BaseModel):
    id: UUID
    receipt_id: UUID
    line_item_id: UUID | None = None
    field_name: str
    extractor_source: str
    extracted_value: str | None = None
    confidence_score: float  # between 0.0 and 1.0
    flagged_reason: FlaggedReason
    status: Status
    ...
```

This is the "please a human look at this" ticket. `FlaggedReason` and
`Status` are enums (fixed sets of allowed string values — like a TypeScript
`type FlaggedReason = "low_confidence" | "conflicting_extractions" | ...`):

- `FlaggedReason` answers **why** the row exists: `low_confidence`,
  `conflicting_extractions`, `validation_failed`, `illegible`, `manual_flag`.
- `Status` answers **where it stands right now**: `pending`, `resolved`,
  `rejected`.

These two are deliberately independent. A row can be `rejected` (a human
looked and said "no, ignore this") without that being permanent — the schema
docstring literally says `rejected` is reversible, not terminal. It's like a
support ticket that got marked "won't fix" but can still be reopened later —
"won't fix" isn't the same axis as "still open vs. closed."

**Real-life analogy:** `ExtractionReview` is a sticky note stuck to a filed
document that says "Jan, please double check this number, the two auditors
who checked it disagree." The note has a reason (*why* it's flagged) and a
status (*is Jan done with it yet*) — two separate things you'd track
separately on any real sticky note or ticketing system like Jira.

**Pydantic itself, for JS devs:** a Pydantic `BaseModel` is what you'd get if
you merged a `zod` schema and the TypeScript `interface` it checks into a
*single* thing. In TS you write the shape once and check it separately
(`ReceiptSchema.parse(json)` returns a typed object). In Pydantic,
`Receipt(**fields)` *is* the validation step — construction succeeds only if
every required field is present and every type passes, and what you get
back is already the typed object, not a separate parse step.

---

## 4. Extract: `src/etl/extract/`

Extract is the "hire some interns and hand them the receipt photo" stage —
the part of the pipeline the newspaper analogy in §1 calls "the reporters."
What's built today is the *interface and orchestration*: the shared contract
every intern (extractor) must follow, and the code that runs however many
interns the config lists and hands back a uniform list of their guesses.
The interns themselves — real OCR engines, real vision-LLM calls — don't
exist yet (that's Phase 5/7). Today's tests use hand-written stub interns
instead, which is deliberate: it proves the orchestration logic works
correctly *before* any real, flaky, network-dependent adapter exists to
muddy that signal.

**Real-life analogy:** this is like writing the newspaper's editorial
process — "every reporter files a story in this exact format, an editor
collects all the filed stories into one folder" — and testing that process
with placeholder stand-in reporters who always file predictable, canned
copy, before the newsroom hires a single real reporter. You're proving the
*process* works, independent of whether any specific reporter is any good.

### 4.1 `types.py` — what one intern's guess looks like

```python
class ExtractionResult(BaseModel):
    source: str
    candidate: Candidate
    confidence: Confidence
```

One `ExtractionResult` is one extractor's full guess at one receipt:
`source` is which intern it was (e.g. `"claude-vision-v2"`,
`"tesseract-ocr"`), `candidate` is their guessed field values, and
`confidence` is how sure they were about each one.

**JS/TS analogy:**

```ts
interface ExtractionResult {
  source: string;
  candidate: Candidate;
  confidence: Confidence;
}
```

`Candidate` mirrors `Store`/`Receipt`/`LineItem` from §3, but with the
server-assigned `id` fields removed:

```python
class Candidate(BaseModel):
    store: CandidateStore
    receipt: CandidateReceipt
    line_items: list[CandidateLineItem]
```

**Why not just reuse `Store`/`Receipt`/`LineItem` directly?** Those generated
types *require* a real `id` (a `UUID`, not optional) — because in the real
schema, every row has one. But an extractor's guess exists *before* anyone
saves anything anywhere. No database assigns it an ID yet. Only
`vela-api` mints real IDs, the same rule you'll see again in §6.1's
ID-stripping logic later. So `Candidate*` types keep the same shape, minus
the fields that don't exist yet at guessing time.

**Real-life analogy:** this is the difference between a job applicant's
resume (no employee ID yet — that's assigned on hire) and an HR system's
employee record (has one). Same person, same information, but one of them
literally cannot have an ID field filled in yet because the thing that
assigns IDs hasn't acted on it.

`Confidence` mirrors `Candidate`'s shape exactly, field-for-field, so it's
easy to walk both in parallel later:

```python
class Confidence(BaseModel):
    store: FieldConfidence  # e.g. {"name": 0.9}
    receipt: FieldConfidence  # e.g. {"total": 0.95}
    line_items: list[FieldConfidence]  # one dict per line item, same order
```

**Why does `line_items` have to stay in the same order as
`Candidate.line_items`?** Because there's no other way to say "this
confidence score belongs to *that* line item" — there's no shared ID to join
on yet (see above). `Confidence.line_items[2]` describes
`Candidate.line_items[2]`, purely by list position. Transform's `reconcile()`
(§5.1) actually enforces this at runtime: it raises an error immediately if
the two lists are different lengths, rather than silently misaligning
guesses to the wrong items.

### 4.2 `protocol.py` — the contract every intern must follow

```python
class Extractor(Protocol):
    def extract(self, image: Image) -> ExtractionResult | ExtractionReview: ...
```

A Python `Protocol` is structural typing — "anything with an `extract`
method matching this signature counts, no explicit `implements` keyword
needed."

**JS/TS analogy:** this is exactly a TypeScript `interface` used
structurally:

```ts
interface Extractor {
  extract(image: Image): ExtractionResult | ExtractionReview;
}
```

Just like TS, you never write `class TesseractExtractor implements
Extractor` — any class with a matching `extract` method satisfies the
protocol automatically, the same "duck typing, but checked" TypeScript gives
you.

**The interesting part is the return type: a union, not just
`ExtractionResult`.** Most of the time, an extractor returns a normal guess.
But sometimes an extractor can't produce *any* guess at all — the photo is
too blurry to read, or the vendor's API call failed outright. In that case,
there's no `candidate` to build, so returning a half-empty `ExtractionResult`
would be misleading. Instead, the extractor returns an `ExtractionReview`
row directly — the same "please a human look at this" ticket type from §3 —
skipping the guess-and-reconcile step entirely, because there was never
anything to reconcile.

**Real-life analogy:** normally a reporter files a story. But if a reporter
gets to the scene and the building burned down before they could interview
anyone, they don't file a half-written story with blanks in it — they call
the editor directly and say "I've got nothing, flag this as a dead lead,"
which goes straight into the "needs attention" pile instead of the normal
editorial review queue.

### 4.3 `orchestrator.py` — running however many interns the config lists

```python
def run_extractors(
    extractors: list[Extractor], image: Image
) -> list[ExtractionResult | ExtractionReview]:
    return [extractor.extract(image) for extractor in extractors]
```

**JS/TS analogy:**

```ts
function runExtractors(extractors: Extractor[], image: Image) {
  return extractors.map((e) => e.extract(image));
}
```

This is deliberately the entire function — one line. The design doc makes
this point directly, and `docs/DECISIONS.md` echoes it: "run one extractor"
and "run five extractors" are **the exact same code path**. There's no
`if (extractors.length === 1) { ... } else { ... }` branch anywhere.
`.map()` over a list of 1 behaves identically to `.map()` over a list of 5
— nothing about this function needs to know or care how many interns are on
the story, or why the config assigned that number.

**Real-life analogy:** the assignment editor's process for "hand this story
to however many reporters are on duty today" doesn't change based on
whether one or five reporters showed up for the shift. The same instruction
— "go cover this, file it in this format" — goes out identically either way.

**`split_results()` — sorting the folder before it reaches the newsroom
editor.** Transform's `reconcile()` (§5.1) only knows how to compare actual
guesses against each other — it has no idea what to do with a "dead lead,
nothing to report" ticket mixed into that pile. So before Transform ever
sees the results, `split_results()` separates the two:

```python
def split_results(results):
    extraction_results = [r for r in results if isinstance(r, ExtractionResult)]
    reviews = [r for r in results if isinstance(r, ExtractionReview)]
    return extraction_results, reviews
```

The real guesses go on to `reconcile()`. The "extraction failed entirely"
tickets skip straight to the final review pile, merged in later at
`emit()`-time (§5.4) — no different in the end from a review row that
`reconcile()` or `validate()` would have produced.

**A real gap this surfaced, worth knowing about:** the "extraction failed
entirely" ticket needs a `field_name` and `flagged_reason`. At the time
this section's first draft named the gap, `vela-core`'s schema only defined
sentinels for "one field is wrong" and "one line item is wrong/missing" —
nothing for "the whole receipt failed, there's no candidate at all."
`vela-core` resolved
this by adding a fourth sentinel (`field_name="receipt"` +
`flagged_reason="extraction_failed"`, see `docs/DECISIONS.md`'s
"Total-failure `ExtractionReview` shape" entry for the full story) — a good
example of how `vela-etl` and `vela-core` stay in sync: building `vela-etl`
surfaced a real gap, `vela-core` fixed it at the source of truth, and the
standing submodule-pin-update rule from §2.2 pulled the fix back in.

### 4.4 What's deliberately *not* here yet

No real extractor exists yet — no `pytesseract`, no AWS Textract, no
Claude/GPT-4V vision calls. `tests/test_orchestrator.py` uses small
hand-written stub classes instead, each just returning a canned
`ExtractionResult` or `ExtractionReview` without doing any real image
processing.

**Why test against stubs instead of waiting for a real adapter?** This is
the same reasoning as mocking an HTTP call in a frontend test — you want to
prove "does my orchestration logic correctly loop over N extractors and
handle both return types" *without* that test also depending on network
flakiness, API rate limits, or OCR accuracy. Those are separate concerns,
tested separately, later (Phase 5/7), against real vendors.

---

## 5. Transform: `src/etl/transform/`

Transform is four **pure functions** — meaning: no network calls, no
database, no file reads/writes, no randomness, no hidden state. Same input,
every time, produces the same output. Nothing here can fail because a
server went down or a lock held a file.

**JS/TS analogy:** picture a chain of Redux reducers, or an
Array `.map()`/`.filter()`/`.reduce()` pipeline — each step is
`(state) => newState`, nothing mutates anything behind your back, and you
could run the exact same function a thousand times in a unit test with zero
setup.

The four functions run strictly in this order:

```txt
reconcile → validate → shape → emit
```

### 5.1 `reconcile.py` — "did the interns agree?"

Input: a list of `ExtractionResult`s — one per "intern" (extractor) that
looked at the same receipt. Each `ExtractionResult` carries that intern's
guessed field values (`candidate`) plus how confident they were in each guess
(`confidence`).

The rule, field by field:

```python
if len(set(values)) == 1:
    resolved[field] = values[0]   # everyone agreed (or there was only one guess)
else:
    # one review row per disagreeing intern, flagged "conflicting_extractions"
```

**JS/TS analogy:**

```ts
const distinct = new Set(values);
if (distinct.size === 1) {
  resolved[field] = values[0];
} else {
  values.forEach((v, i) => flagForReview(field, sources[i], v));
}
```

Notice the code comment in `reconcile.py` calls out something subtle: "one
extractor ran" and "all extractors agreed" are **the same code path** —
`len(set([x])) == 1` is trivially true for a list of one. This means
`reconcile()` never has to ask "how many extractors ran this time?" — it
just looks at whatever list it receives. Adding a third intern to the
process later requires zero changes to this function.

**Real-life analogy:** this is exactly jury deliberation. If all 12 jurors
agree on a verdict, that's the verdict — whether it took one juror stating
the obvious or twelve independently arriving at the same place, the *process*
that recognizes "we have consensus" is identical. If they don't agree, you
don't average their opinions or pick a "majority winner" — you get a hung
jury, and it goes back for more scrutiny. `reconcile()` does the same thing:
no averaging, no majority vote, no silent "just trust the more confident one"
— disagreement always produces an explicit flag for a human, never a guess.

**Line items are the hard part.** Two interns rarely list items in the same
order. If intern A writes down "Milk, Bread, Eggs" and intern B writes
"Bread, Milk, Eggs2%," you can't just compare `items[0]` to `items[0]` — that
would compare "Milk" to "Bread" and wrongly conclude they disagree on
everything.

So `reconcile()` matches line items by **description text similarity**, not
list position — using Python's `difflib.SequenceMatcher`, which is the same
idea as an npm package like `string-similarity` or `fuzzysort`. "Paracetmol
500mg" (typo) and "Paracetamol 500mg" score high enough (above a 0.9
threshold) that `reconcile()` treats them as the same item, so a
single-character OCR typo doesn't spuriously generate a
`missing_line_item` review row. Two genuinely different products stay
separate, because their similarity score falls well below that threshold.

**Real-life analogy:** this is how a store manager reconciles two cashiers'
tally sheets from the same shift — not by comparing line 1 to line 1, but by
matching "oh, this $4.99 entry on both sheets is clearly the same candy bar,"
even if one cashier wrote "Snickers" and the other wrote "Snickers Bar."

### 5.2 `validate.py` — "does the math actually check out?"

This runs *only* on the values `reconcile()` already trusted — it's a second,
independent pass, not a retry of the first.

It checks pure arithmetic:

- `quantity * unit_price == line_total`, for every line item.
- All line items sum to the receipt's subtotal (or `total` minus tax, if
  `reconcile()` never resolved a subtotal).
- Required fields (like `total`, `date`, `transaction_ref`) aren't missing.

**JS/TS analogy:** this is a form-validation function — like a Formik/Zod
`.superRefine()` cross-field check — that doesn't throw on the first
problem, but instead collects every issue into a list:

```ts
function validate(resolved): ValidationError[] {
  const errors = [];
  if (qty * price !== lineTotal) errors.push({ field: "line_total", ... });
  return errors;
}
```

One nice detail: when a check fails, the review row it creates carries a
**confidence score describing how far off the value was**, not just a flat
"this is wrong." A receipt whose total is off by 2 cents due to rounding
gets a *high* confidence score (meaning: probably still right, just a
rounding artifact) — while a total off by 40% gets a low one. This uses a
blend of "how big is the gap in absolute terms" (a 2-cent gap on any receipt
is negligible) and "how big is the gap relative to the number" (once it's
past the negligible range).

**Real-life analogy:** this is the receptionist at a pharmacy re-adding up
your total at checkout, separately from what the register already computed —
catching a scanner glitch (an item scanned twice, a price that didn't
update) that nobody upstream would have flagged as "the two cashiers
disagreed," because there was only one cashier and one register. The math
itself is the second opinion here, not another person.

### 5.3 `shape.py` — "put it in the real, official folder"

By now, `resolved` is just a plain Python `dict` — flexible, but not yet a
guaranteed-valid `Receipt`. `shape()` converts it into the real typed
records from `src/etl/types/`:

```python
receipt = Receipt(
    id=receipt_id,
    store_id=store_id,
    content_hash="",
    extras=extras or None,
    **columns,
)
```

**JS/TS analogy:** `dict` here is like a loose `Record<string, unknown>` or
`any`-typed object you've been passing around — flexible during intermediate
processing, but risky to trust. `Receipt(**columns)` is the moment you
finally run it through `ReceiptSchema.parse(obj)` and get back something
`Receipt`-typed you can trust for the rest of the program. `**columns` is
Python's spread syntax for function *arguments* — the same idea as JS's
`{ id, storeId, ...columns }` object spread, just used to fill in named
parameters instead of building an object literal.

Two other jobs happen here:

**Splitting known fields from `extras`.** Anything in the resolved data that
matches a real `Receipt` column goes into that column. Anything that
doesn't — some store-specific footer field, say — falls into the `extras`
catch-all instead of being silently dropped.

**Real-life analogy:** imagine a government form with fixed boxes (name,
date, amount) plus a "notes" field at the bottom. If your receipt has a
field like "loyalty points earned" that has no dedicated box on the form, it
doesn't get thrown away — it goes in the notes field so it's still on
record, just not as a first-class searchable column.

**Computing `content_hash`.** This is a SHA-256 fingerprint:

```python
raw = "|".join([store_id, transaction_ref, date, total])
content_hash = hashlib.sha256(raw.encode()).hexdigest()
```

The exact same four inputs always produce the exact same hash. This is how
the future `vela-api` will detect "you already uploaded a photo of this
receipt" — not by comparing photos (which would differ pixel-for-pixel
between two photos of the same paper receipt taken at different angles), but
by comparing this fingerprint of the *extracted content*.

**Real-life analogy:** this is a barcode. Two physically different printed
copies of the same concert ticket both scan to the identical barcode value —
the barcode doesn't care about wrinkles or lighting, it identifies *what the
ticket represents*, not the specific piece of paper.

One deliberate ordering detail worth noticing: `shape()` constructs the
`Receipt` object *before* computing `content_hash`, specifically so that
Pydantic's required-field check runs first. If a receipt is missing its
`transaction_ref`, you get a loud, clear error right there — instead of
silently hashing the literal string `"None"` into a fingerprint that looks
valid but isn't.

### 5.4 `emit.py` — "put it in the outbox"

The simplest file in the whole codebase — no logic, just gathers everything
into one bundle:

```python
@dataclass
class EmitResult:
    store: Store
    receipt: Receipt
    line_items: list[LineItem]
    extraction_reviews: list[ExtractionReview]
```

**JS/TS analogy:** a `dataclass` is roughly a TypeScript `interface` with a
constructor auto-generated for you — think of it as a strongly-typed object
literal with a fixed, named shape, rather than `{ ...a, ...b }` improvised
inline every time.

`emit()`'s only job is `extraction_reviews = reconcile_reviews + validate_reviews`
— merging the two separate "please review this" lists from steps 1 and 2
into one combined list, then packaging everything together. It's the
envelope-stuffing step: put the finished letter, the invoice, and any
"please initial here" sticky notes into one envelope, ready to be handed to
the mail room (Load).

---

## 6. Load: `src/etl/load/`

Load is the only stage that does real I/O — it's the one part of this whole
pipeline that can fail because of the network, a downed server, or bad
credentials. Everything before this point stayed pure and local. This is
where it finally leaves the building.

### 6.1 `LoadClient` — the mail room

```python
class LoadClient:
    def __init__(self, base_url, *, client=None):
        self._client = client or httpx.Client(base_url=base_url)
```

`httpx` is Python's `axios`/`fetch` — an HTTP client library. `LoadClient`
wraps it in a small class with exactly one real method:

```python
def ingest(self, store, receipt, extraction_reviews=None) -> IngestionResult:
    payload = {
        "store": store.model_dump(mode="json", exclude={"id"}),
        "receipt": receipt.model_dump(mode="json", exclude={"id", "store_id"}),
    }
    if extraction_reviews:
        payload["extraction_reviews"] = [...]
    response = self._client.post("/ingestions", json=payload)
```

`.model_dump(mode="json", exclude={"id"})` turns the typed `Store`/`Receipt`
object back into a plain JSON-safe dict, but *deliberately drops the `id`
field*. This is the Python equivalent of:

```ts
const { id, ...rest } = store;
JSON.stringify(rest);
```

**Why strip the ID?** `vela-etl` has no database connection anywhere in this
whole codebase — it can't mint a real, permanent primary key. Only
`vela-api`, which does hold the real database connection, may assign real
IDs. Sending a made-up ID from this side would be like a customer filling
in their own order number on a form that only the receiving office should
stamp — it's not this side's job, and the receiving office wouldn't trust
it anyway.

**Real-life analogy:** when you drop off a passport application, you don't
write in your own passport number. The passport office assigns that number
when it processes your application. You submit the *content* (name, photo,
address). It returns the *identifier*.

### 6.2 The response: a discriminated union, not one shape

```python
IngestionResult = Created | Duplicate | ValidationError
```

```python
@dataclass
class Created:
    status: Literal["created"] = "created"
    receipt_id: str = ""
    store_id: str = ""


@dataclass
class Duplicate:
    existing_receipt_id: str
    status: Literal["duplicate"] = "duplicate"


@dataclass
class ValidationError:
    errors: list[str]
    status: Literal["validation_error"] = "validation_error"
```

**TS analogy — this maps almost 1:1:**

```ts
type IngestionResult =
  | { status: "created"; receiptId: string; storeId: string }
  | { status: "duplicate"; existingReceiptId: string }
  | { status: "validation_error"; errors: string[] };
```

Same pattern as a Redux action union, or a `fetch` wrapper that returns
`{ ok: true, data } | { ok: false, error }` instead of throwing. The type
checker forces the caller to handle every branch — there's no way to
accidentally read `.receipt_id` off a `Duplicate` result.

`ingest()` maps HTTP status codes onto these three outcomes:

| HTTP status | Meaning | Result |
| --- | --- | --- |
| `201 Created` | New receipt accepted | `Created(receipt_id, store_id)` |
| `409 Conflict` | `content_hash` already exists in the real DB | `Duplicate(existing_receipt_id)` |
| `422 Unprocessable` | Payload shape/values rejected | `ValidationError(errors)` |
| anything else 4xx/5xx | Genuinely unexpected | raises an exception |

**This is where `content_hash` finally gets checked.** `shape()` computed
the fingerprint, but `vela-etl` itself has no way to know if that fingerprint
already exists somewhere — it has no database to look in. `vela-api` is the
only thing that *can* answer that question, because it's the only thing with
a real connection to the real table. So Load sends the hash along and simply
reacts to whatever `vela-api` says back.

**Real-life analogy:** this is exactly like mailing a package with a tracking
barcode you generated yourself, but only the receiving warehouse's scanner
can tell you "hey, we already have a package with this exact barcode on our
shelf." You don't get to declare a duplicate yourself — you can only ask, and
trust the answer.

### 6.3 `mock_api/app.py` — a stand-in office, since the real one doesn't exist yet

`vela-api` (the real receiving office) doesn't exist yet. So instead of
blocking all of Load's development and testing on that, this repo ships a
tiny **fake version** of it: a standalone FastAPI app that implements the
same three outcomes using an in-memory Python dict instead of a real
database:

```python
_seen_content_hashes: dict[str, str] = {}

if content_hash in _seen_content_hashes:
    return JSONResponse(status_code=409, content={...})
```

**JS/TS analogy:** this is the same idea as running `msw` (Mock Service
Worker) or `json-server` during frontend development before a real backend
exists — you build and test against a believable fake, and the intent from
day one is that swapping the fake's URL for the real backend's URL later
requires *zero* code changes on the calling side.

Run it locally with `uv run poe mock-api` (port `2222`). The design doc's own
build order states this explicitly: `vela-core` (schema) first, then
`vela-etl` built against this mock, then `vela-api` for real — at which point
`vela-etl` "switches over with no code change."

**Real-life analogy:** it's a training simulator for a pilot before they fly
a real plane — the simulator behaves like the real cockpit closely enough
that skills transfer directly, and switching from simulator to real aircraft
doesn't require re-learning the controls.

### 6.4 Tests: `respx`, not the mock app

`tests/test_load_client.py` doesn't start the FastAPI mock app for its
automated tests — that's reserved for manual, by-hand testing. Instead it
uses `respx`, a library that intercepts `httpx` calls directly in-process.

**JS/TS analogy:** this is the same relationship as `nock` (intercepts
`fetch`/`axios` calls without a real server) vs. actually running a
`json-server` process — `nock`/`respx` is faster and needs no real process,
which is exactly why it's what CI uses, while the standalone mock app is for
a human to manually click through an end-to-end flow.

---

## 7. Putting it all together, one more time

```txt
                    ┌─────────────┐
receipt photo  →    │   Extract   │  →  ExtractionResult(s) or ExtractionReview
                    └─────────────┘     (one per "intern"/extractor; real
                                          adapters NOT BUILT YET — stub
                                          interns only)
                                                   │
                                        split_results() separates the two
                                                   │
                                                   ▼
                    ┌─────────────┐
                    │  reconcile  │  agree → resolved value
                    │             │  disagree → review row(s)
                    └─────────────┘
                                                   │
                                                   ▼
                    ┌─────────────┐
                    │  validate   │  arithmetic/required-field checks
                    │             │  → more review rows
                    └─────────────┘
                                                   │
                                                   ▼
                    ┌─────────────┐
                    │   shape     │  → real Store/Receipt/LineItem
                    │             │  → content_hash fingerprint
                    └─────────────┘
                                                   │
                                                   ▼
                    ┌─────────────┐
                    │    emit     │  → one EmitResult bundle
                    └─────────────┘
                                                   │
                                                   ▼
                    ┌─────────────┐
                    │ LoadClient  │  → POST /ingestions
                    │  .ingest()  │  → Created | Duplicate | ValidationError
                    └─────────────┘
                                                   │
                                                   ▼
                                          vela-api (real, not built yet)
                                          or mock_api (stand-in, built)
```

**Extract** = the interns filing their stories (or calling in "I've got
nothing" when the scene was unworkable) — today, canned stub interns for
testing, not real reporters.

**Transform** = the editor reconciling multiple reporters' drafts and
fact-checking the numbers, entirely on paper, no phone calls made.

**Load** = the delivery truck taking the approved, final page to the
press — the only step that actually leaves the building, and today it drives
to a training-simulator press (`mock_api`) because the real press
(`vela-api`) doesn't exist yet.

Nothing currently drives a photo through Extract → Transform → Load
automatically in one call — that end-to-end wiring is future work (Phase 6).
Each stage today has its own tests and works correctly in isolation, the
same way you'd trust each Lego brick individually before you assemble the
whole set into the final model.
