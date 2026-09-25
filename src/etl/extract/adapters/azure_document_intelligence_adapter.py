"""Azure Document Intelligence adapter (`prebuilt-receipt` model, F0 tier).

The vendor's response is already receipt-shaped, so this is mostly field
renaming into Candidate*. Confidence comes from the vendor's native
per-field confidence score (tier 2 of DESIGN-V3.md's three confidence
origins), not derived — Azure reports it directly per field.

Total extraction failure (no documents returned, vendor API error) returns
an ExtractionReview row directly instead of raising, per the Extractor
Protocol — there's no candidate to reconcile in that case.

A real F0 endpoint response, not just the documented schema, surfaced two
gaps:

- Azure's `Items[]` entries don't always include `Price` (unit price), even
  when `Quantity` and `TotalPrice` are both present. Defaulting the missing
  price to 0.0 would silently produce a wrong, not just missing, value —
  `unit_price=0.0` next to a correct nonzero `line_total`. Derived instead
  from `TotalPrice / Quantity`, at the RapidOCR adapter's derived-
  confidence tier, not at Azure's (unearned) native confidence for TotalPrice.
- `prebuilt-receipt` has no invoice/transaction-ref field in its schema at
  all — that concept belongs to a different model (`prebuilt-invoice`). It's
  not "sometimes missing", it's structurally never present. `content_hash`
  (computed in Transform's shape() from store+transaction_ref+date+total)
  would otherwise collide across distinct receipts sharing a store, date,
  and total. This adapter builds `transaction_ref` from stable line-item
  content to keep content_hash unique. See `_synthesize_transaction_ref`
  for why it does not also flag this field for review.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime
from io import BytesIO
from typing import Any
from uuid import UUID, uuid4

from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.ai.documentintelligence.models import AnalyzeResult, DocumentField
from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import AzureError

from etl.extract.protocol import Image, confidence_map, extraction_failed_review
from etl.extract.types import (
    Candidate,
    CandidateLineItem,
    CandidateReceipt,
    CandidateStore,
    Confidence,
    ExtractionResult,
)
from etl.types.extraction_review_schema import ExtractionReview

SOURCE = "azure-document-intelligence"

_MODEL_ID = "prebuilt-receipt"
_DEFAULT_CONFIDENCE = 0.5
"""Azure omits `.confidence` on some fields (e.g. ones it derived rather
than located directly). Falls back to a middling score rather than 0.0,
since the value is still vendor-extracted, just without a reported score."""

_DERIVED_UNIT_PRICE_CONFIDENCE = 0.6
"""Same tier as RapidOcrAdapter's _FOUND_CONFIDENCE: this value wasn't
reported by Azure at all, so it can't carry Azure's TotalPrice confidence —
it's arithmetic we did ourselves (TotalPrice / Quantity), the same origin
(DESIGN-V3.md's third confidence tier) the RapidOCR adapter falls back
to entirely."""


class AzureDocumentIntelligenceAdapter:
    def __init__(
        self,
        endpoint: str,
        key: str,
        receipt_id: UUID | None = None,
        *,
        client: DocumentIntelligenceClient | None = None,
    ) -> None:
        # `client` is a test seam — production callers only ever pass
        # endpoint/key and get a real DocumentIntelligenceClient.
        self._client = client or DocumentIntelligenceClient(
            endpoint=endpoint, credential=AzureKeyCredential(key)
        )
        # Local placeholder id, same rationale as RapidOcrAdapter: no real
        # receipt row exists yet at extract time.
        self._receipt_id = receipt_id or uuid4()

    def extract(self, image: Image) -> ExtractionResult | ExtractionReview:
        try:
            poller = self._client.begin_analyze_document(_MODEL_ID, body=BytesIO(image))
            result: AnalyzeResult = poller.result()
        except AzureError as exc:
            return self._failure_review(f"Azure Document Intelligence API error: {exc}")

        if not result.documents:
            return self._failure_review("Azure returned no documents for this image")

        fields = result.documents[0].fields or {}
        # A present TransactionDate field is not the same as a usable date.
        # `_value()` falls back to the field's raw `content` string when
        # Azure reports no typed value, and one real receipt returned a
        # newline-separated fragment that way. CandidateReceipt then raised
        # a pydantic ValidationError, which breaks the Extractor Protocol's
        # promise to return an ExtractionReview rather than raise.
        if _date(_value(fields.get("TransactionDate"))) is None:
            return self._failure_review(
                "Azure returned no usable TransactionDate for this image"
            )

        return self._to_extraction_result(fields)

    def _failure_review(self, notes: str) -> ExtractionReview:
        return extraction_failed_review(
            receipt_id=self._receipt_id, source=SOURCE, notes=notes
        )

    def _to_extraction_result(
        self, fields: dict[str, DocumentField]
    ) -> ExtractionResult:
        store, store_confidence = _extract_store(fields)
        line_items, line_item_confidence = _extract_line_items(fields)
        receipt, receipt_confidence = _extract_receipt(fields, line_items)

        return ExtractionResult(
            source=SOURCE,
            candidate=Candidate(store=store, receipt=receipt, line_items=line_items),
            confidence=Confidence(
                store=store_confidence,
                receipt=receipt_confidence,
                line_items=line_item_confidence,
            ),
        )


def _value(field: DocumentField | None) -> Any:
    if field is None:
        return None
    for attr in (
        "value_string",
        "value_number",
        "value_date",
        "value_time",
        "value_phone_number",
        "value_currency",
    ):
        value = getattr(field, attr, None)
        if value is not None:
            if attr == "value_currency":
                return getattr(value, "amount", None)
            return value
    return getattr(field, "content", None)


def _date(value: Any) -> date | None:
    """Coerces a field value to a date, or None when it is not one. Guards
    the same raw-`content` fallback that `_number()` guards."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is None:
        return None
    try:
        return date.fromisoformat(str(value).strip())
    except (TypeError, ValueError):
        return None


def _number(value: Any) -> float | None:
    """Coerces a field value to a float, or None when it is not numeric.

    `_value()` falls back to a field's raw `content` string when Azure
    reports no typed value, so a money field can arrive as un-parseable OCR
    text. Confirmed against a real receipt: one returned a bare currency
    sign, "₦", which `float()` raised a ValueError on and crashed the
    whole extraction. The Extractor Protocol requires an ExtractionReview
    instead of an exception, so an unusable value becomes None here and the
    caller treats the field as missing."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _confidence(field: DocumentField | None) -> float:
    if field is None:
        return 0.0
    return field.confidence if field.confidence is not None else _DEFAULT_CONFIDENCE


def _extract_store(
    fields: dict[str, DocumentField],
) -> tuple[CandidateStore, dict[str, float]]:
    name = _value(fields.get("MerchantName"))
    address = _value(fields.get("MerchantAddress"))
    phone = _value(fields.get("MerchantPhoneNumber"))

    store = CandidateStore(
        name=str(name) if name else "",
        address=str(address) if address else "",
        phone=str(phone) if phone else None,
    )
    confidence = confidence_map(
        {
            field: _confidence(fields.get(key)) if fields.get(key) is not None else None
            for field, key in (
                ("name", "MerchantName"),
                ("address", "MerchantAddress"),
                ("phone", "MerchantPhoneNumber"),
            )
        }
    )
    return store, confidence


def _extract_line_items(
    fields: dict[str, DocumentField],
) -> tuple[list[CandidateLineItem], list[dict[str, float]]]:
    items_field = fields.get("Items")
    raw_items = getattr(items_field, "value_array", None) or []

    candidate_items: list[CandidateLineItem] = []
    confidence: list[dict[str, float]] = []

    for raw_item in raw_items:
        item_fields: dict[str, DocumentField] = (
            getattr(raw_item, "value_object", None) or {}
        )

        description = _value(item_fields.get("Description")) or ""
        quantity = _value(item_fields.get("Quantity"))
        unit_price = _value(item_fields.get("Price"))
        line_total = _value(item_fields.get("TotalPrice"))

        quantity_float = _number(quantity) or 1.0
        line_total_float = _number(line_total) or 0.0

        unit_price_number = _number(unit_price)
        if unit_price_number is not None:
            unit_price_float = unit_price_number
            unit_price_confidence = _confidence(item_fields.get("Price"))
        elif quantity is not None and quantity_float != 0:
            unit_price_float = round(line_total_float / quantity_float, 2)
            unit_price_confidence = _DERIVED_UNIT_PRICE_CONFIDENCE
        else:
            unit_price_float = 0.0
            unit_price_confidence = None

        item_confidence = confidence_map(
            {
                "description": _confidence(item_fields.get("Description"))
                if item_fields.get("Description") is not None
                else None,
                "quantity": _confidence(item_fields.get("Quantity"))
                if item_fields.get("Quantity") is not None
                else None,
                "line_total": _confidence(item_fields.get("TotalPrice"))
                if item_fields.get("TotalPrice") is not None
                else None,
                "unit_price": unit_price_confidence,
            }
        )

        candidate_items.append(
            CandidateLineItem(
                description=str(description),
                quantity=quantity_float,
                unit_price=unit_price_float,
                line_total=line_total_float,
            )
        )
        confidence.append(item_confidence)

    return candidate_items, confidence


def _synthesize_transaction_ref(line_items: list[CandidateLineItem]) -> str:
    """Stand in for a transaction ref `prebuilt-receipt` never returns.

    Not flagged for review: reconcile() resolves a lone extractor's value
    silently whenever there's nothing to disagree with. validate()'s
    required-field rule only catches an empty/null transaction_ref, not a
    synthetic-but-present one. Showing this to a human reviewer would need a
    new rule in Transform (out of this adapter's scope). This function
    solves the content_hash collision only, not review visibility.

    Content, not vendor confidence, is what content_hash needs to stay
    discriminating: two distinct receipts sharing store + date + total but
    different purchases must not collide. Hashing each item's description,
    quantity, and line_total keeps the ref stable for the same physical
    receipt (deterministic across re-runs) while still varying whenever the
    actual purchase differs.
    """
    basis = "|".join(
        f"{item.description}:{item.quantity}:{item.line_total}" for item in line_items
    )
    digest = hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]
    return f"azure-synthetic-{digest}"


def _extract_receipt(
    fields: dict[str, DocumentField], line_items: list[CandidateLineItem]
) -> tuple[CandidateReceipt, dict[str, float]]:
    transaction_date = _date(_value(fields.get("TransactionDate")))
    # extract() rejects an unusable date before it calls this, so by here the
    # coercion always succeeds. CandidateReceipt.date is non-optional and has
    # no sentinel for a missing date.
    assert transaction_date is not None
    transaction_time = _value(fields.get("TransactionTime"))
    subtotal = _value(fields.get("Subtotal"))
    tax = _value(fields.get("TotalTax"))
    total = _value(fields.get("Total"))

    receipt = CandidateReceipt(
        source_image_id="",
        transaction_ref=_synthesize_transaction_ref(line_items),
        date=transaction_date,
        time=transaction_time,
        subtotal=_number(subtotal),
        vat=_number(tax),
        total=_number(total) or 0.0,
        line_items=line_items or None,
    )
    confidence = confidence_map(
        {
            field: _confidence(fields.get(key)) if fields.get(key) is not None else None
            for field, key in (
                ("date", "TransactionDate"),
                ("time", "TransactionTime"),
                ("subtotal", "Subtotal"),
                ("vat", "TotalTax"),
                ("total", "Total"),
            )
        }
    )
    return receipt, confidence
