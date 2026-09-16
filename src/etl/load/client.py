"""httpx-based client for vela-api's ingestion endpoint, per docs/api/openapi.yaml.

Provisional: vela-api does not exist yet. This client is built against
vela-etl's own stated expectation of the endpoint, not a contract vela-api
has agreed to, and will change if the real implementation diverges.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Self

import httpx

from etl.types.extraction_review_schema import ExtractionReview
from etl.types.receipt_schema import Receipt
from etl.types.store_schema import Store


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
    errors: list[str] = field(default_factory=list)
    status: Literal["validation_error"] = "validation_error"


IngestionResult = Created | Duplicate | ValidationError


class LoadClient:
    """Sends one shaped receipt (plus any review rows) to vela-api."""

    def __init__(self, base_url: str, *, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(base_url=base_url)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def ingest(
        self,
        store: Store,
        receipt: Receipt,
        extraction_reviews: list[ExtractionReview] | None = None,
    ) -> IngestionResult:
        payload: dict[str, Any] = {
            "store": store.model_dump(mode="json", exclude={"id"}),
            "receipt": receipt.model_dump(mode="json", exclude={"id", "store_id"}),
        }
        if extraction_reviews:
            payload["extraction_reviews"] = [
                review.model_dump(mode="json", exclude={"id", "receipt_id"})
                for review in extraction_reviews
            ]

        response = self._client.post("/ingestions", json=payload)

        if response.status_code == 201:
            body = response.json()
            return Created(receipt_id=body["receipt_id"], store_id=body["store_id"])
        if response.status_code == 409:
            body = response.json()
            return Duplicate(existing_receipt_id=body["existing_receipt_id"])
        if response.status_code == 422:
            body = response.json()
            return ValidationError(errors=body.get("errors", []))

        response.raise_for_status()
        raise RuntimeError(f"Unexpected response from vela-api: {response.status_code}")
