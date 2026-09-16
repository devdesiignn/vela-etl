import json

import respx
from httpx import Response

from etl.load import Created, Duplicate, LoadClient, ValidationError
from etl.types.extraction_review_schema import ExtractionReview
from etl.types.receipt_schema import Receipt
from etl.types.store_schema import Store

BASE_URL = "http://mock-vela-api.test"

STORE = Store(
    id="6105a8cf-f678-48da-8ce2-89cfe24fb61a",
    name="Supreme Pharmacy",
    address="12 Ikorodu Rd, Lagos",
)

RECEIPT = Receipt(
    id="44d21dc0-ada2-4b24-9ad4-91800c4922a9",
    store_id="6105a8cf-f678-48da-8ce2-89cfe24fb61a",
    source_image_id="img_full_001",
    transaction_ref="INV-2026-0042",
    date="2026-01-15",
    total=4300.0,
    content_hash="hash_full_receipt_001",
)

REVIEW = ExtractionReview(
    id="6dbdc6dd-37ff-42f9-bb7b-661740759ac4",
    receipt_id="44d21dc0-ada2-4b24-9ad4-91800c4922a9",
    field_name="total",
    extractor_source="extractor-a",
    extracted_value="4300.00",
    confidence_score=0.612,
    flagged_reason="low_confidence",
    status="pending",
)


@respx.mock
def test_ingest_success_returns_created():
    respx.post(f"{BASE_URL}/ingestions").mock(
        return_value=Response(
            201,
            json={
                "status": "created",
                "receipt_id": "44d21dc0-ada2-4b24-9ad4-91800c4922a9",
                "store_id": "6105a8cf-f678-48da-8ce2-89cfe24fb61a",
            },
        )
    )

    with LoadClient(BASE_URL) as client:
        result = client.ingest(STORE, RECEIPT)

    assert isinstance(result, Created)
    assert result.receipt_id == "44d21dc0-ada2-4b24-9ad4-91800c4922a9"


@respx.mock
def test_ingest_sends_store_and_receipt_and_omits_generated_ids():
    route = respx.post(f"{BASE_URL}/ingestions").mock(
        return_value=Response(
            201,
            json={"status": "created", "receipt_id": "x", "store_id": "y"},
        )
    )

    with LoadClient(BASE_URL) as client:
        client.ingest(STORE, RECEIPT)

    payload = json.loads(route.calls.last.request.content)
    assert "id" not in payload["store"]
    assert "id" not in payload["receipt"]
    assert "store_id" not in payload["receipt"]
    assert payload["store"]["name"] == "Supreme Pharmacy"
    assert payload["receipt"]["content_hash"] == "hash_full_receipt_001"
    assert "extraction_reviews" not in payload


@respx.mock
def test_ingest_includes_extraction_reviews_when_present():
    route = respx.post(f"{BASE_URL}/ingestions").mock(
        return_value=Response(
            201,
            json={"status": "created", "receipt_id": "x", "store_id": "y"},
        )
    )

    with LoadClient(BASE_URL) as client:
        client.ingest(STORE, RECEIPT, extraction_reviews=[REVIEW])

    payload = json.loads(route.calls.last.request.content)
    assert len(payload["extraction_reviews"]) == 1
    assert payload["extraction_reviews"][0]["field_name"] == "total"
    assert "id" not in payload["extraction_reviews"][0]
    assert "receipt_id" not in payload["extraction_reviews"][0]


@respx.mock
def test_ingest_duplicate_returns_duplicate():
    respx.post(f"{BASE_URL}/ingestions").mock(
        return_value=Response(
            409,
            json={
                "status": "duplicate",
                "existing_receipt_id": "44d21dc0-ada2-4b24-9ad4-91800c4922a9",
            },
        )
    )

    with LoadClient(BASE_URL) as client:
        result = client.ingest(STORE, RECEIPT)

    assert isinstance(result, Duplicate)
    assert result.existing_receipt_id == "44d21dc0-ada2-4b24-9ad4-91800c4922a9"


@respx.mock
def test_ingest_validation_error_returns_validation_error():
    respx.post(f"{BASE_URL}/ingestions").mock(
        return_value=Response(
            422,
            json={
                "status": "validation_error",
                "errors": ["receipt.total is required"],
            },
        )
    )

    with LoadClient(BASE_URL) as client:
        result = client.ingest(STORE, RECEIPT)

    assert isinstance(result, ValidationError)
    assert result.errors == ["receipt.total is required"]
