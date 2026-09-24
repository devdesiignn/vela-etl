# Debug scripts

Manual tools for working on the Extract adapters against real receipt
photos. Not part of the test suite and not imported by `etl/`.

Real receipt photos are never committed to this repo, per its
data-minimization stance. These scripts read them from a local directory
you point them at. Committed synthetic fixtures live in
`tests/fixtures/extract/` instead.

## `sweep_receipts.py`

Runs `RapidOcrAdapter` over a range of receipt images and writes one result
line per receipt.

```
uv run python scripts/sweep_receipts.py 0 28
```

Writes `debug_sweep_batch_<start>_<end>.txt`, flushed after every receipt,
so partial progress stays readable while it runs.

**Run it in a single process.** Do not run several ranges at once. Nine
concurrent processes produced 22 spurious `Unknown C++ exception from
OpenCV code` failures, plus one process that silently extracted nothing
while exiting 0. See `docs/DECISIONS.md`.

## `dump_ocr_lines.py`

Prints the grouped OCR lines for one image.

```
uv run python scripts/dump_ocr_lines.py <path-to-image>
```

The adapter runs OCR **twice**, on the raw oriented image and on the
`preprocess()`'d one, then parses whichever recovers more fields. This
script prints both passes with their scores and names the one the adapter
parses. Read that line before concluding OCR misread something: the garbled
pass is often the one that loses.
