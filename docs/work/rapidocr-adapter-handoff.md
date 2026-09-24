# RapidOCR adapter — handoff (real-data hardening, in progress)

## Where this fits

Phase 5 of `docs/IMPLEMENTATION-PLAN.md`: two real Extract adapters, proving
the `Extractor` Protocol at both ends of the effort spectrum. Azure Document
Intelligence (the "easy," receipt-shaped-response end) is complete and not
part of this handoff. It carries mocked unit tests plus one confirmed
live-API run against a real receipt, which fixed the
`unit_price`/`transaction_ref` bugs documented in `docs/DECISIONS.md`. **It has NOT been swept across the full
28-receipt sample the way RapidOCR is being stress-tested in this
handoff** — if broader real-data confidence in the Azure adapter matters,
that is still open work, just not this handoff's subject.

The RapidOCR adapter (the "hard," raw-text end) is the subject of this
handoff. Its architecture is sound and its mocked unit tests all pass, but
real-receipt testing (28 sample photos on the user's Desktop, at
`C:\Users\Muizzz\Desktop\receipt-data`) surfaced a series of real bugs, most
now fixed. Two open, harder problems remain, described below. **Do not
report this adapter as "done" until it succeeds against a real sweep of all
28 receipts** — every earlier claim of success in this session's history was
wrong until the next real-data test proved otherwise. Confirm before
reporting, every time.

## Why RapidOCR replaced pytesseract

Originally this adapter wrapped `pytesseract`, which shells out to a
separate `tesseract-ocr` system binary — not installable via `uv`/`pip`,
needing a manual OS-level install on every machine that runs this code
(dev, CI, production container). The user pushed back on that deployment
cost. `RapidOCR` (PyPI: `rapidocr`, needs `onnxruntime` too) is a pure
Python package with no system dependency — matches this repo's "install
one thing, get a working pipeline" convention. See
`docs/DECISIONS.md`'s "OCR engine" entries (search for "RapidOCR") for the
full reasoning, including the two adapter-pairing decisions already logged
there.

**This swap is why the file layout looks the way it does:**
- `src/etl/extract/adapters/rapidocr_adapter.py` — the adapter (was
  `pytesseract_adapter.py`).
- `src/etl/extract/adapters/ocr_text_parser.py` — the OCR-engine-agnostic
  text parser (was `tesseract_text_parser.py`, renamed since it never
  actually depended on Tesseract specifically — it just parses a flat text
  block into `ParsedReceipt`/`ParsedLineItem`, regardless of which OCR
  engine produced that text).
- `src/etl/extract/preprocess.py` — OpenCV deskew/contrast/threshold
  pipeline, now also has `normalize_orientation()` (see below).

## How the two files relate (a real point of confusion earlier this session)

`ocr_text_parser.py` does NOT talk to "the next phase" of the pipeline. The
call direction is:

1. `rapidocr_adapter.py` calls RapidOCR, gets back scattered text boxes.
2. It reconstructs physical printed lines from those boxes'
   coordinates (`_group_boxes_into_lines`, see below), producing one flat
   text string.
3. It calls `ocr_text_parser.parse(that_string)`, which returns a
   `ParsedReceipt`.
4. The adapter turns that `ParsedReceipt` into the real `ExtractionResult`
   (or `ExtractionReview` on failure) that the rest of the pipeline
   consumes.

`ocr_text_parser.py` is a pure text-in/structured-data-out helper, with zero
knowledge of OCR engines, box geometry, or images. That separation is
intentional and should stay — don't merge these two files' responsibilities.

## Bugs found and fixed this session (all confirmed against real receipts)

Only running the adapter against real photos caught each of these — the
mocked test suite never exercised these paths. A regression test now
exists for each, in `tests/test_ocr_text_parser.py` or
`tests/test_rapidocr_adapter.py`.

1. **`_ITEM_LINE_NO_QTY` read the wrong regex capture group.**
   `match.group(1)` was the `description` named group (Python numbers named
   groups too), not the money capture (`group(2)`). Crashed with
   `ValueError: could not convert string to float: '1 TONGUE SCRAPER'` on a
   real receipt. Fixed in `ocr_text_parser.py`.

2. **EXIF photo orientation went ignored, scrambling text order.**
   `cv2.imdecode()`/`np.frombuffer()` read raw pixel data and silently
   ignore EXIF orientation tags. A phone photo tagged "rotate 90° on
   display" (EXIF orientation 6 — normal for phones held upright) decodes
   as sideways pixels. RapidOCR then reads real, legible text but computes
   box positions against the wrong axes, producing garbled reading order.
   Fixed via `preprocess.py`'s new `normalize_orientation()`
   (`PIL.ImageOps.exif_transpose`), called both inside `preprocess()` and
   directly in the adapter before its raw-bytes OCR pass.

3. **RapidOCR returns each text region as a separate box, not grouped by
   printed line** — e.g. `"SUB TOTAL"` and `"8,800.00"` come back as two
   separate strings, never one `"SUB TOTAL  8,800.00"` line the way
   Tesseract's block-text output would give it. `ocr_text_parser.py`'s
   regexes assume label and value share one line. Fixed by
   `rapidocr_adapter._group_boxes_into_lines()`, which uses box (x,y)
   coordinates to reconstruct physical lines before handing text to the
   parser. **This session already rewrote this function once, after a
   second bug (#5 below). Read its current docstring — don't trust old
   descriptions of "how it works."**

4. **Date format gaps.** `"September 12. 2025"` (OCR misread the comma
   after the day as a period) didn't match any `_DATE_CANDIDATE` pattern,
   and even once captured, `%B %d, %Y` wasn't in `_DATE_FORMATS` at all
   (only `%b %d, %Y`, abbreviated month). Also `"03-Jan-26"`
   (day-abbreviatedMonth-2digitYear, hyphens) wasn't covered by any
   pattern. Both fixed in `ocr_text_parser.py` — `_DATE_CANDIDATE`'s regex
   and `_DATE_FORMATS`' tuple. **Expect more date format gaps** across the
   remaining receipts — this parser's date coverage is still incomplete,
   confirmed by real data, not exhaustive by design review.

5. **Row-grouping's span-growing bug.** The first version of
   `_group_boxes_into_lines()` let a row's vertical span grow to
   `max(row_bottom, new_box_bottom)` every time a box joined it. One
   anomalous box (confirmed: 1377px tall against a 316px median height on
   one real receipt) blew up the row's span and swallowed every subsequent
   box into one garbled line. Rewritten to compare each box's center only
   to the *previous* box's center (sorted order), starting a new row when
   the gap exceeds half the receipt's median box height — no
   unboundedly-growing span. **This fix does NOT fully solve the row-
   grouping problem** — see Open Problem 1 below.

6. **Subtotal-as-total fallback.** Some real receipts print only a
   "SUB TOTAL" line with no separate "TOTAL" line at all (no tax/discount
   shown as its own line). `ParsedReceipt.total` stayed `None` in that case.
   Fixed: after the main parse loop, if `total is None and subtotal is not
   None`, `total = subtotal`.

7. **Totals-line item-count prefix.** Some receipts print an item count
   before the actual money value on the same totals line, e.g.
   `"SUB TOTAL 6.00 8,800.00"` (6 items, then 8,800.00 total).
   `_apply_totals_line` used `re.search` (first match) instead of
   `re.findall` + take the last match, so it picked up `6.00` as the total.
   Fixed.

## Open Problem 1: multi-column receipt layouts (not fixed)

Receipt `20260113_204229.jpg` (from `debug_sweep_batch_3_6.txt`, or rerun
`debug_sweep_incremental.py 3 6`) prints its line items in **side-by-side
columns**, not top-to-bottom rows. The current output for this receipt is
totally wrong (`store="Four thousand, one hundred and fifty naira only."`,
`items=0`) — confirmed by the user directly reading the sweep output.
Dumping raw box coordinates reveals why: boxes for `"1 ROBB"`,
`"CHARCOAL 120g"`, `"1 COLGATE"`, `"1 ABONIKI"`,
`"1 MARS CHOCOLATE 1,100.00"` all sit at nearly the same y-range
(1046–2061) but very different x (1223 through 1642+). These are
different *items*, arranged as columns, not fields of one item on one row.

`_group_boxes_into_lines()`'s whole model (group by vertical position,
order left-to-right within a row) assumes a single-column, top-to-bottom
receipt layout. It cannot represent a genuinely multi-column layout without
a different algorithm — e.g. detecting distinct x-position clusters first,
then treating each cluster as its own sub-column to walk top-to-bottom
separately, then deciding how those columns' text relates (same item's
fields spread across a row, vs. genuinely separate items in parallel lists).
This is a real, nontrivial layout-detection problem, not a regex gap.
**Not fixed. Needs a real design decision**, possibly: detect this case and
route to `ExtractionReview` rather than guessing, rather than trying to
solve general multi-column reconstruction.

## Open Problem 2: a completely different line-item text format (not fixed)

Receipts `20260105_142619.jpg` and `20260109_144924.jpg` (same batch) use
this format per item, confirmed from real OCR output. The user directly
confirmed both are wrong in the current sweep output: receipt 105's real
store name is "ROTAMEDIC GRA OFFICE," not the `"RM"` stray fragment the
header heuristic grabbed, and its `items` count should not be 0 or 1 for
either receipt (105 has 5 items, 109 has 2).

```
#1:EVERYMAN MULTIVITAMIN
(3) Unit × N2,750.00 N8,250.00
#2: STREPSILS BY 2PCS
(4) Unit × N450.00 N1,800.00
```

Description on one line (prefixed `#N:`), quantity/unit price/total on the
*next* line (`(qty) Unit × price total`), with a Naira symbol (`N`) prefix
on money values that isn't in `_MONEY`'s currency-symbol character class
(only `$₦£€` — note `₦` the real Naira symbol is there, but this receipt's
OCR read it as a literal `N`, since `₦` easily misreads as `N`). Neither
`_ITEM_LINE` nor `_ITEM_LINE_NO_QTY` matches this two-line-per-item shape at
all — the parser currently finds 0 or 1 line items on these receipts
(should be 5 and 2, respectively).

This needs either: (a) a third item-line pattern spanning two consecutive
lines (`#N:` line lookahead + qty/price on the following line), or (b) a
broader restructure of `_parse_item_line` to look at line pairs, not just
one line at a time. Also add `N` (bare capital N, not just `₦`) to
`_MONEY`'s currency-prefix character class — cheap, real, and independently
useful regardless of the two-line fix.

**A separate, related bug on the same two receipts**: the header heuristic
(`_apply_header_line`'s `header_candidates` collection in
`ocr_text_parser.py`) grabbed a stray OCR fragment, `"RM"`, as
receipt 105's store name instead of the real name, `"ROTAMEDIC GRA
OFFICE"` (visible one line below it in the merged text). `"RM"` is
presumably a stray or partially-merged box from `_group_boxes_into_lines`,
not a real header line. Test whether this is a row-grouping artifact (a
fragment that should have merged into the real store-name row) before
assuming the header heuristic itself needs to change.

## Full sweep completed (all 28 receipts, serial)

**The directory holds 28 receipts, not 27** — earlier notes undercounted by one.

A full serial sweep of all 28 now exists. Run it with
`uv run python debug_sweep_incremental.py 0 28` from the repo root, in a
**single process**. Do not run batches concurrently: nine parallel processes
produced 22 spurious `Unknown C++ exception from OpenCV code` crashes plus
one batch that silently processed nothing while exiting 0. The same receipts
pass cleanly when run serially. OpenCV/ONNX thread-pool oversubscription is
the cause. Setting `OMP_NUM_THREADS=1` / `OPENCV_NUM_THREADS=1` before
importing cv2/onnxruntime would likely make parallelism safe, but that is
untested. Memory is a stable ~664 MB across a full serial run, with no leak,
so run length costs nothing — batching buys no resource headroom.

**This concurrency sensitivity is worth treating as a real open question for
production**, not just a debug-tooling detail: a pipeline extracting several
receipts in parallel may hit the same exceptions.

### Result after the review-gating fix: 10 OK, 18 REVIEW

Before the fix the same 28 receipts gave 7 clean, 14 wrong-but-reported-OK,
and 7 flagged. Half the sample was silently bad data reaching `vela-api` as
valid extractions.

### Fixed: review gating was incomplete (`rapidocr_adapter.extract`)

`CandidateReceipt.total` is non-nullable, so the adapter coerced a missing total to
`0.0` at the `CandidateReceipt(...)` call and shipped it as a success. The old
guard rejected only when total **and** line items were both missing, so a
receipt with 13 items and no total passed straight through. The adapter
already knew — its confidence map scored the total `None` — and reported
success anyway.

Three gates now exist, each with a regression test in
`tests/test_rapidocr_adapter.py`:

1. `total is None` with line items present → review (7 receipts).
2. Line items empty with a total present → review (4 receipts).
3. `total == 0.0` with line items present → review (2 receipts).

Gate 3 was **missed on the first attempt** and caught only by re-running the
real sweep: a total that parses as a literal `0.00` is a different path from
a missing total, and an `is None` test never sees it. One such receipt
carried 16 line items against a zero total. This is the handoff's standing
rule proving itself — mocked tests passed, the fix looked complete, and the
real data disagreed.

## Open problems: the four still outstanding

The review-gating fix closed one bug class. Four problems remain open. Do not
read "10 OK" as 10 accurate extractions — 2 of those 10 are wrong and pass
every gate:

| Receipt | Reports | Reality |
|---|---|---|
| `20260105_142619.jpg` | `store='RM'`, 1 item | ROTAMEDIC GRA OFFICE, 5 items |
| `20260326_194507.jpg` | `store='Sor & thop'`, 1 item | garbled store name |

Problems 1 and 2 have their own sections above. Problems 3 and 4 follow.

## Open Problem 3 (new): store names corrupted by OCR

Confirmed across the sweep: `'SUPREME PHARMACY & STORI'` (truncated E) on
several photos, `'IPREME PH\RMACY & STORI'`, `'MARTEIYE Scan & shrop'`,
`'Sor & thop'`, `'fire'`, `'RM'`, and `'0001:3647-4'` (a transaction ref
grabbed as the store name). Note `20260423_211452.jpg` reads
`'SUPREME PHARMACY & STORE'` **correctly** — the same merchant, same chain,
read correctly on one photo and wrongly on four others.

This matters beyond cosmetics: store name feeds `content_hash`, so
corruption here breaks duplicate detection. This is the OCR-typo class
earlier notes logged as a non-urgent future idea. Real data says it hits
store names directly and deserves promotion.

Fixing it likely means fuzzy matching against known merchants, or a
spellcheck pass over recognized text. Both are engineering choices about
accuracy, not parser bugs.

## Open Problem 4 (new): the gates catch zero, never "too few"

The three review gates added this session reject a receipt with **no** line
items or **no** total. They cannot detect a receipt that parsed 1 of 5 items,
so a partial read still ships as a success. This is a real limit of the
gating fix, not a bug in it.

A sum of line-item totals compared against the parsed total would catch this
class. Not implemented, and it needs care around receipts with tax or
discounts, where the sum legitimately differs from the total.

## Process notes for whoever picks this up

- **Never trust "it works" without a fresh real-data run.** This session
  repeatedly declared success after mocked tests passed, then found a new
  real bug on the very next real receipt. Real receipts, not synthetic
  fixtures, are the actual acceptance bar for this adapter specifically —
  its whole purpose is proving OCR works on real photos.
- **`tests/fixtures/extract/` is empty.** No real fixture *images* exist
  anywhere in this repo — every test so far uses either hand-written text
  strings (`tests/test_ocr_text_parser.py`) or the user's real Desktop
  photos directly (never committed, per this repo's data-minimization
  stance). Per `docs/IMPLEMENTATION-PLAN.md`'s testing strategy, this repo
  wants synthetic/faker-generated fixture *images*, not real personal
  receipts, committed to `tests/fixtures/`. None exist yet. Worth doing
  once real bugs stop surfacing so fast, so regressions get caught without
  needing the user's real photos every time.
- **Debug scratch files**: `debug_sweep_incremental.py` (reusable batch
  runner) and `debug_sweep_batch_3_6.txt` (last run's output) are still in
  the repo root, untracked. Clean up ad-hoc `debug_*.txt` dumps after
  reading them — this session left several stray ones the user had to
  point out.
- **OCR-typo tolerance** (e.g. "Fiday" for "Friday"): the user raised this
  as a future idea — a language-tool/spellcheck pass over recognized text.
  No one started this yet. The user does not consider it urgent, and it
  would not fix most of the bugs above anyway — those are format/geometry
  bugs, not spelling.
- Full verification loop after every change: `uv run poe test`,
  `uv run poe lint`, `uv run pyright src` must all stay clean before
  calling a fix done. Passing that loop is still not sufficient on its
  own. Re-run the real-receipt batch too, every time.
