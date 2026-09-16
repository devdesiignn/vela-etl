"""Standalone mock of vela-api's ingestion endpoint, per docs/api/openapi.yaml.

Provisional: vela-api does not exist yet. This mock exists so vela-etl can be
built and tested end-to-end before it does, and will change if vela-api's
real implementation diverges from this sketch.
"""

from __future__ import annotations

from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI(title="vela-api ingestion mock")

_seen_content_hashes: dict[str, str] = {}


@app.post("/ingestions")
async def create_ingestion(request: Request) -> JSONResponse:
    payload = await request.json()

    store = payload.get("store")
    receipt = payload.get("receipt")
    if not store or not receipt:
        return JSONResponse(
            status_code=422,
            content={
                "status": "validation_error",
                "errors": ["store and receipt are both required"],
            },
        )

    content_hash = receipt.get("content_hash")
    if not content_hash:
        return JSONResponse(
            status_code=422,
            content={
                "status": "validation_error",
                "errors": ["receipt.content_hash is required"],
            },
        )

    if content_hash in _seen_content_hashes:
        return JSONResponse(
            status_code=409,
            content={
                "status": "duplicate",
                "existing_receipt_id": _seen_content_hashes[content_hash],
            },
        )

    receipt_id = str(uuid4())
    store_id = str(uuid4())
    _seen_content_hashes[content_hash] = receipt_id

    return JSONResponse(
        status_code=201,
        content={"status": "created", "receipt_id": receipt_id, "store_id": store_id},
    )
