import pytest
from pydantic import ValidationError

from etl.types.extraction_review_schema import ExtractionReview
from etl.types.line_item_schema import LineItem
from etl.types.receipt_schema import Receipt
from etl.types.store_schema import Store


def test_store_accepts_valid_instance():
    store = Store(
        id="6105a8cf-f678-48da-8ce2-89cfe24fb61a",
        name="Supreme Pharmacy",
        address="12 Ikorodu Rd, Lagos",
    )
    assert store.name == "Supreme Pharmacy"


def test_store_rejects_missing_required_field():
    with pytest.raises(ValidationError):
        Store(name="Supreme Pharmacy")  # missing required id, address


def test_receipt_accepts_valid_instance_with_nested_line_items():
    receipt = Receipt(
        id="44d21dc0-ada2-4b24-9ad4-91800c4922a9",
        store_id="6105a8cf-f678-48da-8ce2-89cfe24fb61a",
        source_image_id="img_full_001",
        transaction_ref="INV-2026-0042",
        date="2026-01-15",
        total=4300.0,
        content_hash="hash_full_receipt_001",
        line_items=[
            LineItem(
                id="d1e2f3a4-0000-0000-0000-000000000001",
                receipt_id="44d21dc0-ada2-4b24-9ad4-91800c4922a9",
                description="Paracetamol 500mg",
                quantity=2,
                unit_price=150.0,
                line_total=300.0,
                line_order=1,
            )
        ],
    )
    assert receipt.total == 4300.0
    assert receipt.line_items[0].description == "Paracetamol 500mg"


def test_receipt_rejects_missing_required_field():
    with pytest.raises(ValidationError):
        Receipt(
            store_id="6105a8cf-f678-48da-8ce2-89cfe24fb61a",
            source_image_id="img_full_001",
            transaction_ref="INV-2026-0042",
            date="2026-01-15",
            total=4300.0,
            content_hash="hash_full_receipt_001",
        )  # missing required id


def test_line_item_accepts_valid_instance():
    line_item = LineItem(
        id="d1e2f3a4-0000-0000-0000-000000000001",
        receipt_id="44d21dc0-ada2-4b24-9ad4-91800c4922a9",
        description="Paracetamol 500mg",
        quantity=2,
        unit_price=150.0,
        line_total=300.0,
        line_order=1,
    )
    assert line_item.description == "Paracetamol 500mg"


def test_line_item_rejects_missing_required_field():
    with pytest.raises(ValidationError):
        LineItem(
            receipt_id="44d21dc0-ada2-4b24-9ad4-91800c4922a9",
            description="Paracetamol 500mg",
            quantity=2,
            unit_price=150.0,
            line_total=300.0,
            line_order=1,
        )  # missing required id


def test_extraction_review_accepts_valid_instance():
    review = ExtractionReview(
        id="6dbdc6dd-37ff-42f9-bb7b-661740759ac4",
        receipt_id="44d21dc0-ada2-4b24-9ad4-91800c4922a9",
        field_name="total",
        extractor_source="extractor-a",
        extracted_value="4300.00",
        confidence_score=0.612,
        flagged_reason="low_confidence",
        status="pending",
    )
    assert review.flagged_reason.value == "low_confidence"


def test_extraction_review_rejects_missing_required_field():
    with pytest.raises(ValidationError):
        ExtractionReview(
            receipt_id="44d21dc0-ada2-4b24-9ad4-91800c4922a9",
            field_name="total",
            extractor_source="extractor-a",
            extracted_value="4300.00",
            confidence_score=0.612,
            flagged_reason="low_confidence",
        )  # missing required status
