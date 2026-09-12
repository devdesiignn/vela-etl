# receipt-etl

Receipt photos in, structured data out — extraction, confidence scoring, and manual review.

## What lives here

- The pipeline that turns a receipt photo into structured data: merchant, date, line items, quantities, prices, tax, total.
- Confidence scoring on each extracted field, and a manual-review path for anything the pipeline isn't confident about.
- Extracts only the fields defined in [`receipt-core`](https://github.com/devdesiignn/receipt-core)'s schema — nothing beyond that is captured or retained.
- Writes structured data and review resolutions via [`receipt-api`](https://github.com/devdesiignn/receipt-api), rather than connecting to the database directly.

## What this does not do

- v1 handles printed receipts only. POS screenshots and handwritten receipts are out of scope for this version.
- Does not expose a public API or dashboard.
- Does not connect to `receipt-core`'s database directly — all writes go through `receipt-api`.
- Does not chase perfect accuracy on every receipt; extraction accuracy is tracked and reported as a number, with a manual-review path for the rest.

## Status

Early setup. Pipeline design in progress.

## Related repos

Part of the [Receipt Intelligence Platform](https://github.com/devdesiignn/receipt-intelligence-platform). Depends on [`receipt-core`](https://github.com/devdesiignn/receipt-core)'s schema, and writes through [`receipt-api`](https://github.com/devdesiignn/receipt-api).
