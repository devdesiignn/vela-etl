# receipt-etl

Receipt photos in, structured data out — extraction, confidence scoring, manual review, and anonymization.

## What lives here

- The pipeline that turns a receipt photo into structured data: merchant, date, line items, quantities, prices, tax, total.
- Confidence scoring on each extracted field, and a manual-review path for anything the pipeline isn't confident about.
- Anonymization/redaction of personal details (names, card numbers, exact addresses) as part of the pipeline, before data reaches storage.
- Writes into the shared schema defined in [`receipt-core`](https://github.com/devdesiignn/receipt-core).

## What this does not do

- v1 handles printed receipts only. POS screenshots and handwritten receipts are out of scope for this version.
- Does not expose a public API or dashboard.
- Does not chase perfect accuracy on every receipt; extraction accuracy is tracked and reported as a number, with a manual-review path for the rest.

## Status

Early setup. Pipeline design in progress.

## Related repos

Part of the [Receipt Intelligence Platform](https://github.com/devdesiignn/receipt-intelligence-platform). Depends on [`receipt-core`](https://github.com/devdesiignn/receipt-core).
