from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import Any


def _existing_refs(value: Any) -> list[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return list(value)
    if isinstance(value, (Mapping, str)) and value:
        return [value]
    return []


def runtime_row_evidence_fields(
    *,
    source_dataset: str,
    entity_id: str,
    item_type: str,
    observed_at: str,
    existing_refs: Any = None,
) -> dict[str, Any]:
    """Reference the concrete runtime row without treating catalog data as evidence."""
    dataset = str(source_dataset or "").strip()
    entity = str(entity_id or "").strip()
    item_kind = str(item_type or "").strip()
    observation = str(observed_at or "").strip()
    if not all((dataset, entity, item_kind, observation)):
        return {}
    identity = "\x1f".join((dataset, entity, item_kind, observation))
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    runtime_ref = {
        "type": "dataset_row",
        "source_dataset": dataset,
        "source_record_id": f"record-{digest}",
        "observed_at": observation,
    }
    return {"evidence_refs": [*_existing_refs(existing_refs), runtime_ref]}


__all__ = ("runtime_row_evidence_fields",)
