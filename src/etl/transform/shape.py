"""Shape: split resolved values into schema columns vs. extras, assign
line_order, compute content_hash, and construct the generated pydantic types.

Per DESIGN-V3.md's "Transform pipeline" -> "3. Shape".

Validator choice: pydantic only, not jsonschema. Constructing Store/Receipt/
LineItem below (generated directly from vela-core's vendored JSON Schema via
datamodel-code-generator) already enforces that schema's required fields,
types, and constraints -- that construction IS the re-validation step. Running
jsonschema against the same vendored .schema.json files on top would duplicate
the same check through a second code path with no independent guarantee,
since both ultimately read from the same source files.
"""

from __future__ import annotations

import hashlib
from uuid import uuid4

from etl.types.line_item_schema import LineItem
from etl.types.receipt_schema import Receipt
from etl.types.store_schema import Store


def _content_hash(
    *, store_id: str, transaction_ref: str, date: object, total: object
) -> str:
    # Join order/format is load-bearing: it must stay stable across runs,
    # since this is the sole dedup key vela-api checks receipts against.
    raw = "|".join([str(store_id), str(transaction_ref), str(date), str(total)])
    return hashlib.sha256(raw.encode()).hexdigest()


def shape(
    resolved: dict,
    *,
    store_id: str,
    receipt_id: str,
) -> tuple[Store, Receipt, list[LineItem]]:
    """Split resolved candidate values into shaped Store/Receipt/LineItem records."""
    store_fields = resolved.get("store", {})
    store = Store(id=store_id, **store_fields)

    receipt_fields = dict(resolved.get("receipt", {}))
    excluded_receipt_fields = {"id", "store_id", "content_hash", "line_items", "extras"}
    # Match against both the Python attribute name and any declared alias --
    # not just model_fields' attribute names. datamodel-code-generator can
    # regenerate Receipt with a field alias (e.g. a camelCase JSON key
    # mapped to a snake_case attribute) if vela-core's schema ever declares
    # one; matching attribute names alone would silently misroute an
    # alias-spelled key into extras instead of the real column, with no
    # error. Today Receipt declares no aliases, so this is currently
    # equivalent to the attribute-name-only check, but stays correct if
    # that changes on a future schema regen.
    known_receipt_fields: set[str] = set()
    for name, info in Receipt.model_fields.items():
        if name in excluded_receipt_fields:
            continue
        known_receipt_fields.add(name)
        if info.alias:
            known_receipt_fields.add(info.alias)
        if info.validation_alias:
            known_receipt_fields.add(str(info.validation_alias))

    columns = {k: v for k, v in receipt_fields.items() if k in known_receipt_fields}
    extras = {k: v for k, v in receipt_fields.items() if k not in known_receipt_fields}

    # id/receipt_id/line_order are always assigned here, never taken from
    # item_fields -- CandidateLineItem doesn't declare them today, but
    # resolved["line_items"] can (in the single-extractor pass-through path
    # in reconcile.py) be a raw item.model_dump() rather than a
    # field-by-field reconciled dict. If CandidateLineItem's schema is ever
    # extended to include one of these names, an unfiltered **item_fields
    # spread would collide with the explicit kwarg below and raise
    # "got multiple values for keyword argument". Filtering them out here
    # keeps shape() correct regardless of what CandidateLineItem declares.
    excluded_line_item_fields = {"id", "receipt_id", "line_order"}
    line_items = [
        LineItem(
            id=str(uuid4()),
            receipt_id=receipt_id,
            line_order=index,
            **{
                k: v
                for k, v in item_fields.items()
                if k not in excluded_line_item_fields
            },
        )
        for index, item_fields in enumerate(resolved.get("line_items", []), start=1)
    ]

    # Construct the Receipt before computing content_hash from its fields --
    # this makes pydantic's required-field validation run first, so a
    # receipt missing a required field (e.g. transaction_ref left unresolved
    # by reconcile()) raises here rather than silently hashing the string
    # "None" for a field that was never actually present.
    receipt = Receipt(
        id=receipt_id,
        store_id=store_id,
        content_hash="",
        extras=extras or None,
        **columns,
    )
    receipt.content_hash = _content_hash(
        store_id=store_id,
        transaction_ref=receipt.transaction_ref,
        date=receipt.date,
        total=receipt.total,
    )

    return store, receipt, line_items
