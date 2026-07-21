from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping, Sequence
from typing import Any

from app.services.security_context import sign_server_payload


_ATTESTATION_VERSION = "hmac-sha256-v1"
_ATTESTATION_PURPOSE = "control-room-runtime-evidence-v1"
_SIGNED_FIELDS = (
    "type",
    "source_dataset",
    "source_record_id",
    "source_locator",
    "source_row_hash",
    "observed_at",
    "attestation_version",
)


def _existing_refs(value: Any) -> list[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return list(value)
    if isinstance(value, (Mapping, str)) and value:
        return [value]
    return []


def _row_hash(row: Mapping[str, Any]) -> str:
    payload = json.dumps(
        dict(row),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _attestation_payload(reference: Mapping[str, Any]) -> bytes:
    return json.dumps(
        {field: reference.get(field) for field in _SIGNED_FIELDS},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _attestation(reference: Mapping[str, Any]) -> str:
    return sign_server_payload(
        _attestation_payload(reference),
        purpose=_ATTESTATION_PURPOSE,
    )


def verified_runtime_row_reference(value: Mapping[str, Any]) -> bool:
    locator = value.get("source_locator")
    if not isinstance(locator, Mapping):
        return False
    record_id = str(value.get("source_record_id") or "").strip()
    locator_value = str(locator.get("value") or "").strip()
    required = (
        str(value.get("source_dataset") or "").strip(),
        record_id,
        str(locator.get("relation") or "").strip(),
        str(locator.get("field") or "").strip(),
        locator_value,
        str(value.get("source_row_hash") or "").strip(),
        str(value.get("observed_at") or "").strip(),
        str(value.get("server_attestation") or "").strip(),
    )
    if (
        value.get("type") != "dataset_row"
        or value.get("attestation_version") != _ATTESTATION_VERSION
        or not all(required)
        or record_id != f"record-{locator_value}"
    ):
        return False
    try:
        return hmac.compare_digest(
            str(value["server_attestation"]), _attestation(value)
        )
    except RuntimeError:
        return False


def runtime_row_evidence_fields(
    *,
    source_dataset: str,
    source_row: Mapping[str, Any],
    locator_field: str,
    observed_at: str,
    locator_relation: str | None = None,
    existing_refs: Any = None,
) -> dict[str, Any]:
    """Attest a locator taken from a row already retrieved by the server."""
    dataset = str(source_dataset or "").strip()
    field = str(locator_field or "").strip()
    relation = str(locator_relation or dataset).strip()
    observation = str(observed_at or "").strip()
    if not isinstance(source_row, Mapping) or field not in source_row:
        return {}
    raw_record_id = source_row.get(field)
    if isinstance(raw_record_id, (Mapping, Sequence)) and not isinstance(
        raw_record_id, (str, bytes, bytearray)
    ):
        return {}
    locator_value = str(raw_record_id if raw_record_id is not None else "").strip()
    if not all((dataset, relation, field, locator_value, observation)):
        return {}
    record_id = f"record-{locator_value}"
    runtime_ref = {
        "type": "dataset_row",
        "source_dataset": dataset,
        "source_record_id": record_id,
        "source_locator": {
            "relation": relation,
            "field": field,
            "value": locator_value,
        },
        "source_row_hash": _row_hash(source_row),
        "observed_at": observation,
        "attestation_version": _ATTESTATION_VERSION,
    }
    try:
        runtime_ref["server_attestation"] = _attestation(runtime_ref)
    except RuntimeError:
        return {}
    return {"evidence_refs": [*_existing_refs(existing_refs), runtime_ref]}


__all__ = ("runtime_row_evidence_fields", "verified_runtime_row_reference")
