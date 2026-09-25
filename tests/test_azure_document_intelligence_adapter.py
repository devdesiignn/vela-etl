from datetime import date
from unittest.mock import MagicMock
from uuid import UUID

from azure.ai.documentintelligence.models import (
    AnalyzeResult,
    DocumentField,
    DocumentFieldType,
)
from azure.core.exceptions import AzureError

from etl.extract.adapters.azure_document_intelligence_adapter import (
    AzureDocumentIntelligenceAdapter,
)
from etl.extract.types import ExtractionResult
from etl.types.extraction_review_schema import ExtractionReview, FlaggedReason

RECEIPT_ID = UUID("44d21dc0-ada2-4b24-9ad4-91800c4922a9")


def _string_field(value: str, confidence: float | None = 0.9) -> DocumentField:
    return DocumentField(
        type=DocumentFieldType.STRING,
        value_string=value,
        confidence=confidence,
        content=value,
    )


def _number_field(value: float, confidence: float | None = 0.9) -> DocumentField:
    return DocumentField(
        type=DocumentFieldType.NUMBER,
        value_number=value,
        confidence=confidence,
        content=str(value),
    )


def _date_field(value: date, confidence: float | None = 0.9) -> DocumentField:
    return DocumentField(
        type=DocumentFieldType.DATE,
        value_date=value,
        confidence=confidence,
        content=value.isoformat(),
    )


def _item_object_field(
    description: str,
    quantity: float,
    total_price: float,
    price: float | None = None,
) -> DocumentField:
    value_object = {
        "Description": _string_field(description),
        "Quantity": _number_field(quantity),
        "TotalPrice": _number_field(total_price),
    }
    if price is not None:
        value_object["Price"] = _number_field(price)
    return DocumentField(
        type=DocumentFieldType.OBJECT,
        value_object=value_object,
        confidence=0.9,
    )


def _items_array_field(items: list[DocumentField]) -> DocumentField:
    return DocumentField(
        type=DocumentFieldType.ARRAY, value_array=items, confidence=0.9
    )


def _mock_analyze_result(fields: dict[str, DocumentField] | None) -> AnalyzeResult:
    document = MagicMock()
    document.fields = fields
    result = MagicMock(spec=AnalyzeResult)
    result.documents = [document] if fields is not None else []
    return result


def _adapter_with_mocked_client(analyze_result: AnalyzeResult | Exception):
    mock_client = MagicMock()
    if isinstance(analyze_result, Exception):
        mock_client.begin_analyze_document.side_effect = analyze_result
    else:
        mock_poller = MagicMock()
        mock_poller.result.return_value = analyze_result
        mock_client.begin_analyze_document.return_value = mock_poller

    return AzureDocumentIntelligenceAdapter(
        endpoint="https://example.cognitiveservices.azure.com",
        key="fake-key",
        receipt_id=RECEIPT_ID,
        client=mock_client,
    )


def test_extract_returns_extraction_result_for_well_formed_response():
    fields = {
        "MerchantName": _string_field("Supreme Pharmacy"),
        "MerchantAddress": _string_field("12 Ikorodu Rd, Lagos"),
        "TransactionDate": _date_field(date(2026, 1, 15)),
        "Subtotal": _number_field(800.0),
        "TotalTax": _number_field(40.0),
        "Total": _number_field(840.0),
        "Items": _items_array_field(
            [_item_object_field("Paracetamol 500mg", 2, 300.0, price=150.0)]
        ),
    }
    adapter = _adapter_with_mocked_client(_mock_analyze_result(fields))

    result = adapter.extract(b"fake-image-bytes")

    assert isinstance(result, ExtractionResult)
    assert result.source == "azure-document-intelligence"
    assert result.candidate.store.name == "Supreme Pharmacy"
    assert result.candidate.receipt.date.isoformat() == "2026-01-15"
    assert result.candidate.receipt.total == 840.0
    assert len(result.candidate.line_items) == 1
    assert result.candidate.line_items[0].description == "Paracetamol 500mg"
    assert result.candidate.line_items[0].unit_price == 150.0
    assert result.confidence.receipt["total"] == 0.9
    assert result.confidence.line_items[0]["quantity"] == 0.9
    assert result.confidence.line_items[0]["unit_price"] == 0.9


def test_extract_uses_default_confidence_when_field_omits_it():
    fields = {
        "MerchantName": _string_field("Store", confidence=None),
        "TransactionDate": _date_field(date(2026, 1, 15)),
        "Total": _number_field(100.0),
    }
    adapter = _adapter_with_mocked_client(_mock_analyze_result(fields))

    result = adapter.extract(b"fake-image-bytes")

    assert isinstance(result, ExtractionResult)
    assert result.confidence.store["name"] == 0.5


def test_extract_returns_review_when_no_documents_returned():
    adapter = _adapter_with_mocked_client(_mock_analyze_result(None))

    result = adapter.extract(b"fake-image-bytes")

    assert isinstance(result, ExtractionReview)
    assert result.receipt_id == RECEIPT_ID
    assert result.field_name == "receipt"
    assert result.flagged_reason == FlaggedReason.extraction_failed


def test_extract_returns_review_on_azure_api_error():
    adapter = _adapter_with_mocked_client(AzureError("boom"))

    result = adapter.extract(b"fake-image-bytes")

    assert isinstance(result, ExtractionReview)
    assert result.flagged_reason == FlaggedReason.extraction_failed
    assert "boom" in (result.extractor_notes or "")


def test_extract_returns_review_when_transaction_date_missing():
    fields = {
        "MerchantName": _string_field("Store"),
        "Total": _number_field(100.0),
    }
    adapter = _adapter_with_mocked_client(_mock_analyze_result(fields))

    result = adapter.extract(b"fake-image-bytes")

    assert isinstance(result, ExtractionReview)
    assert result.flagged_reason == FlaggedReason.extraction_failed


def test_extract_handles_missing_optional_fields_gracefully():
    fields = {
        "TransactionDate": _date_field(date(2026, 1, 15)),
        "Total": _number_field(50.0),
    }
    adapter = _adapter_with_mocked_client(_mock_analyze_result(fields))

    result = adapter.extract(b"fake-image-bytes")

    assert isinstance(result, ExtractionResult)
    assert result.candidate.store.name == ""
    assert result.candidate.line_items == []


def test_extract_derives_unit_price_when_azure_omits_price_field():
    fields = {
        "TransactionDate": _date_field(date(2026, 1, 15)),
        "Total": _number_field(999.0),
        "Items": _items_array_field(
            [_item_object_field("Bread", 3, 999.0)]  # no `price=` -> Price absent
        ),
    }
    adapter = _adapter_with_mocked_client(_mock_analyze_result(fields))

    result = adapter.extract(b"fake-image-bytes")

    assert isinstance(result, ExtractionResult)
    item = result.candidate.line_items[0]
    assert item.unit_price == 333.0
    assert item.quantity == 3
    assert item.line_total == 999.0
    assert result.confidence.line_items[0]["unit_price"] == 0.6


def test_extract_defaults_unit_price_to_zero_when_price_and_quantity_both_missing():
    item_field = DocumentField(
        type=DocumentFieldType.OBJECT,
        value_object={
            "Description": _string_field("Mystery item"),
            "TotalPrice": _number_field(50.0),
        },
        confidence=0.9,
    )
    fields = {
        "TransactionDate": _date_field(date(2026, 1, 15)),
        "Total": _number_field(50.0),
        "Items": _items_array_field([item_field]),
    }
    adapter = _adapter_with_mocked_client(_mock_analyze_result(fields))

    result = adapter.extract(b"fake-image-bytes")

    assert isinstance(result, ExtractionResult)
    item = result.candidate.line_items[0]
    assert item.quantity == 1.0
    assert item.unit_price == 0.0
    assert "unit_price" not in result.confidence.line_items[0]


def test_extract_synthesizes_transaction_ref_deterministically():
    fields = {
        "TransactionDate": _date_field(date(2026, 1, 15)),
        "Total": _number_field(300.0),
        "Items": _items_array_field(
            [_item_object_field("Paracetamol 500mg", 2, 300.0, price=150.0)]
        ),
    }
    adapter = _adapter_with_mocked_client(_mock_analyze_result(fields))
    other_adapter = _adapter_with_mocked_client(_mock_analyze_result(fields))

    result = adapter.extract(b"fake-image-bytes")
    other_result = other_adapter.extract(b"fake-image-bytes")

    assert isinstance(result, ExtractionResult)
    assert isinstance(other_result, ExtractionResult)
    assert result.candidate.receipt.transaction_ref != ""
    assert (
        result.candidate.receipt.transaction_ref
        == other_result.candidate.receipt.transaction_ref
    )
    assert "transaction_ref" not in result.confidence.receipt


def test_extract_synthesizes_different_refs_for_different_line_items():
    fields_a = {
        "TransactionDate": _date_field(date(2026, 1, 15)),
        "Total": _number_field(300.0),
        "Items": _items_array_field(
            [_item_object_field("Paracetamol 500mg", 2, 300.0, price=150.0)]
        ),
    }
    fields_b = {
        "TransactionDate": _date_field(date(2026, 1, 15)),
        "Total": _number_field(300.0),
        "Items": _items_array_field(
            [_item_object_field("Vitamin C", 1, 300.0, price=300.0)]
        ),
    }
    adapter_a = _adapter_with_mocked_client(_mock_analyze_result(fields_a))
    adapter_b = _adapter_with_mocked_client(_mock_analyze_result(fields_b))

    result_a = adapter_a.extract(b"fake-image-bytes")
    result_b = adapter_b.extract(b"fake-image-bytes")

    assert isinstance(result_a, ExtractionResult)
    assert isinstance(result_b, ExtractionResult)
    assert (
        result_a.candidate.receipt.transaction_ref
        != result_b.candidate.receipt.transaction_ref
    )


def _content_only_field(content: str) -> DocumentField:
    """A field Azure returned with no typed value, only raw OCR content.
    `_value()` falls back to that content, so it can be any string at all."""
    return DocumentField(type=DocumentFieldType.STRING, confidence=0.9, content=content)


def test_extract_returns_review_when_the_date_is_unparseable():
    """Regression, from a live sweep of 28 real receipts: Azure returned a
    TransactionDate whose only value was the raw OCR fragment "6\n0\n26".
    The field was present, so the existing guard passed, and CandidateReceipt
    then raised a pydantic ValidationError. The Extractor Protocol requires
    an ExtractionReview instead of an exception."""
    fields = {
        "MerchantName": _string_field("Corner Store"),
        "TransactionDate": _content_only_field("6\n0\n26"),
        "Total": _number_field(500.0),
    }
    adapter = _adapter_with_mocked_client(_mock_analyze_result(fields))

    result = adapter.extract(b"image-bytes")

    assert isinstance(result, ExtractionReview)
    assert result.flagged_reason == FlaggedReason.extraction_failed


def test_extract_survives_a_money_field_that_is_not_a_number():
    """Regression, from the same live sweep: Azure returned a bare currency
    sign as a money field's content. float() raised a ValueError and crashed
    the whole extraction. An unusable amount now reads as missing."""
    fields = {
        "MerchantName": _string_field("Corner Store"),
        "TransactionDate": _date_field(date(2026, 6, 6)),
        "Total": _number_field(500.0),
        "Subtotal": _content_only_field("\u20a6"),
    }
    adapter = _adapter_with_mocked_client(_mock_analyze_result(fields))

    result = adapter.extract(b"image-bytes")

    assert isinstance(result, ExtractionResult)
    assert result.candidate.receipt.subtotal is None
    assert result.candidate.receipt.total == 500.0
