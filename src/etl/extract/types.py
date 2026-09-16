"""Pre-Load candidate types and ExtractionResult, per DESIGN-V3.md's Extract section.

Candidate* models mirror the generated Store/Receipt/LineItem schema types but
omit server-assigned ids (and content_hash/line_order, computed later in
Transform's shape() stage) since extractors produce data before anything is
persisted by vela-api.
"""

from __future__ import annotations

from datetime import date as date_aliased
from datetime import time as time_aliased
from typing import Any

from pydantic import BaseModel, Field


class CandidateStore(BaseModel):
    name: str
    address: str
    phone: str | None = None
    email: str | None = None
    website: str | None = None


class CandidateLineItem(BaseModel):
    description: str
    quantity: float
    unit_price: float
    line_total: float


class CandidateReceipt(BaseModel):
    source_image_id: str
    transaction_ref: str
    transaction_ref_label: str | None = None
    register_ref: str | None = None
    date: date_aliased
    time: time_aliased | None = None
    staff_name: str | None = None
    customer_name: str | None = None
    payment_method: str | None = None
    subtotal: float | None = None
    discount: float | None = None
    vat: float | None = None
    consumption_tax: float | None = None
    total: float
    total_in_words: str | None = None
    extras: dict[str, Any] | None = None
    line_items: list[CandidateLineItem] | None = None


class Candidate(BaseModel):
    store: CandidateStore
    receipt: CandidateReceipt
    line_items: list[CandidateLineItem]


FieldConfidence = dict[str, float]
"""Field name -> confidence score (0.0-1.0), scoped to one section."""


class Confidence(BaseModel):
    store: FieldConfidence = Field(default_factory=dict)
    receipt: FieldConfidence = Field(default_factory=dict)
    line_items: list[FieldConfidence] = Field(default_factory=list)
    """Positional: line_items[i] holds confidence scores for candidate.line_items[i]."""


class ExtractionResult(BaseModel):
    source: str
    candidate: Candidate
    confidence: Confidence
