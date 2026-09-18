from datetime import date
from typing import Any
from uuid import UUID

import pytest
from pydantic import ValidationError

from etl.types.extraction_review_schema import ExtractionReview, FlaggedReason, Status
from etl.types.line_item_schema import LineItem
from etl.types.receipt_schema import Receipt
from etl.types.store_schema import Store


def test_store_accepts_valid_instance():
    store = Store(
        id=UUID("6105a8cf-f678-48da-8ce2-89cfe24fb61a"),
        name="Supreme Pharmacy",
        address="12 Ikorodu Rd, Lagos",
    )
    assert store.name == "Supreme Pharmacy"


def test_store_rejects_missing_required_field():
    fields: dict[str, Any] = {"name": "Supreme Pharmacy"}  # missing id, address
    with pytest.raises(ValidationError):
        Store(**fields)


def test_receipt_accepts_valid_instance_with_nested_line_items():
    receipt = Receipt(
        id=UUID("44d21dc0-ada2-4b24-9ad4-91800c4922a9"),
        store_id=UUID("6105a8cf-f678-48da-8ce2-89cfe24fb61a"),
        source_image_id="img_full_001",
        transaction_ref="INV-2026-0042",
        date=date(2026, 1, 15),
        total=4300.0,
        content_hash="hash_full_receipt_001",
        line_items=[
            LineItem(
                id=UUID("d1e2f3a4-0000-0000-0000-000000000001"),
                receipt_id=UUID("44d21dc0-ada2-4b24-9ad4-91800c4922a9"),
                description="Paracetamol 500mg",
                quantity=2,
                unit_price=150.0,
                line_total=300.0,
                line_order=1,
            )
        ],
    )
    assert receipt.total == 4300.0
    assert receipt.line_items is not None
    assert receipt.line_items[0].description == "Paracetamol 500mg"


def test_receipt_rejects_missing_required_field():
    fields: dict[str, Any] = {
        "store_id": UUID("6105a8cf-f678-48da-8ce2-89cfe24fb61a"),
        "source_image_id": "img_full_001",
        "transaction_ref": "INV-2026-0042",
        "date": date(2026, 1, 15),
        "total": 4300.0,
        "content_hash": "hash_full_receipt_001",
    }  # missing id
    with pytest.raises(ValidationError):
        Receipt(**fields)


def test_line_item_accepts_valid_instance():
    line_item = LineItem(
        id=UUID("d1e2f3a4-0000-0000-0000-000000000001"),
        receipt_id=UUID("44d21dc0-ada2-4b24-9ad4-91800c4922a9"),
        description="Paracetamol 500mg",
        quantity=2,
        unit_price=150.0,
        line_total=300.0,
        line_order=1,
    )
    assert line_item.description == "Paracetamol 500mg"


def test_line_item_rejects_missing_required_field():
    fields: dict[str, Any] = {
        "receipt_id": UUID("44d21dc0-ada2-4b24-9ad4-91800c4922a9"),
        "description": "Paracetamol 500mg",
        "quantity": 2,
        "unit_price": 150.0,
        "line_total": 300.0,
        "line_order": 1,
    }  # missing id
    with pytest.raises(ValidationError):
        LineItem(**fields)


def test_extraction_review_accepts_valid_instance():
    review = ExtractionReview(
        id=UUID("6dbdc6dd-37ff-42f9-bb7b-661740759ac4"),
        receipt_id=UUID("44d21dc0-ada2-4b24-9ad4-91800c4922a9"),
        field_name="total",
        extractor_source="extractor-a",
        extracted_value="4300.00",
        confidence_score=0.612,
        flagged_reason=FlaggedReason.low_confidence,
        status=Status.pending,
    )
    assert review.flagged_reason.value == "low_confidence"


def test_extraction_review_rejects_missing_required_field():
    fields: dict[str, Any] = {
        "receipt_id": UUID("44d21dc0-ada2-4b24-9ad4-91800c4922a9"),
        "field_name": "total",
        "extractor_source": "extractor-a",
        "extracted_value": "4300.00",
        "confidence_score": 0.612,
        "flagged_reason": FlaggedReason.low_confidence,
    }  # missing status
    with pytest.raises(ValidationError):
        ExtractionReview(**fields)
