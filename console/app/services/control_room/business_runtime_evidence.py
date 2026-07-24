from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from .business_evidence_signing import (
    active_evidence_signing_key_id,
    sign_control_room_evidence,
    verify_control_room_evidence,
)
from .business_evidence_binding import runtime_business_binding


_ATTESTATION_VERSION = "hmac-sha256-v4"
_ATTESTATION_PURPOSE = "control-room-runtime-evidence-v1"
_SIGNED_FIELDS = (
    "type",
    "source_dataset",
    "source_system",
    "cartridge",
    "scope_binding",
    "source_record_id",
    "source_locator",
    "source_row_hash",
    "business_binding",
    "observed_at",
    "attestation_version",
    "attestation_purpose",
    "attestation_key_id",
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


def runtime_scope_binding(tenant_id: str, workspace_id: str) -> str:
    scope = "\x1f".join(
        " ".join(str(value or "").strip().casefold().split())
        for value in (tenant_id, workspace_id)
    )
    return hashlib.sha256(scope.encode("utf-8")).hexdigest()


def _attestation_payload(reference: Mapping[str, Any]) -> bytes:
    return json.dumps(
        {field: reference.get(field) for field in _SIGNED_FIELDS},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _attestation(reference: Mapping[str, Any]) -> str:
    return sign_control_room_evidence(
        _attestation_payload(reference),
        purpose=_ATTESTATION_PURPOSE,
        key_id=str(reference.get("attestation_key_id") or ""),
    )


def verified_runtime_row_reference(value: Mapping[str, Any]) -> bool:
    locator = value.get("source_locator")
    if not isinstance(locator, Mapping):
        return False
    record_id = str(value.get("source_record_id") or "").strip()
    locator_value = str(locator.get("value") or "").strip()
    required = (
        str(value.get("source_dataset") or "").strip(),
        str(value.get("source_system") or "").strip(),
        str(value.get("cartridge") or "").strip(),
        str(value.get("scope_binding") or "").strip(),
        record_id,
        str(locator.get("relation") or "").strip(),
        str(locator.get("field") or "").strip(),
        locator_value,
        str(value.get("source_row_hash") or "").strip(),
        value.get("business_binding")
        if isinstance(value.get("business_binding"), Mapping)
        else None,
        str(value.get("observed_at") or "").strip(),
        str(value.get("attestation_key_id") or "").strip(),
        str(value.get("server_attestation") or "").strip(),
    )
    if (
        value.get("type") != "dataset_row"
        or value.get("attestation_version") != _ATTESTATION_VERSION
        or value.get("attestation_purpose") != _ATTESTATION_PURPOSE
        or not all(required)
        or record_id != f"record-{locator_value}"
    ):
        return False
    return verify_control_room_evidence(
        _attestation_payload(value),
        purpose=_ATTESTATION_PURPOSE,
        key_id=str(value["attestation_key_id"]),
        signature=str(value["server_attestation"]),
    )


def canonical_runtime_row_reference(value: Mapping[str, Any]) -> dict[str, Any] | None:
    if not verified_runtime_row_reference(value):
        return None
    canonical = {field: value[field] for field in _SIGNED_FIELDS}
    canonical["source_locator"] = dict(value["source_locator"])
    canonical["business_binding"] = dict(value["business_binding"])
    canonical["server_attestation"] = value["server_attestation"]
    return canonical


def runtime_row_evidence_fields(
    *,
    source_dataset: str,
    source_system: str,
    cartridge: str,
    tenant_id: str | None,
    workspace_id: str,
    source_row: Mapping[str, Any],
    locator_field: str,
    observed_at: str,
    locator_relation: str | None = None,
    existing_refs: Any = None,
    business_observation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Attest a locator taken from a row already retrieved by the server."""
    dataset = str(source_dataset or "").strip()
    system = str(source_system or "").strip()
    cartridge_id = str(cartridge or "").strip()
    tenant = str(tenant_id or "").strip()
    workspace = str(workspace_id or "").strip()
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
    if not all(
        (
            dataset,
            system,
            cartridge_id,
            tenant,
            workspace,
            relation,
            field,
            locator_value,
            observation,
        )
    ):
        return {}
    record_id = f"record-{locator_value}"
    business_binding = runtime_business_binding(
        business_observation or source_row,
        locator_field=field,
        observed_at=observation,
    )
    if business_binding is None:
        return {}
    try:
        runtime_ref = {
            "type": "dataset_row",
            "source_dataset": dataset,
            "source_system": system,
            "cartridge": cartridge_id,
            "scope_binding": runtime_scope_binding(tenant, workspace),
            "source_record_id": record_id,
            "source_locator": {
                "relation": relation,
                "field": field,
                "value": locator_value,
            },
            "source_row_hash": _row_hash(source_row),
            "business_binding": business_binding,
            "observed_at": observation,
            "attestation_version": _ATTESTATION_VERSION,
            "attestation_purpose": _ATTESTATION_PURPOSE,
            "attestation_key_id": active_evidence_signing_key_id(),
        }
        runtime_ref["server_attestation"] = _attestation(runtime_ref)
    except RuntimeError:
        return {}
    return {"evidence_refs": [*_existing_refs(existing_refs), runtime_ref]}


__all__ = (
    "canonical_runtime_row_reference",
    "runtime_row_evidence_fields",
    "runtime_scope_binding",
    "verified_runtime_row_reference",
)
