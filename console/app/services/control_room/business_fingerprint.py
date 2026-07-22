from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from app.services.control_room.business_lineage import parent_references
from app.services.control_room.business_observation_codec import (
    POLICY_FIELDS,
    semantic_surfaces,
)


_IDENTITY_FIELDS = frozenset({"kind", "item_kind"})
_ROOT_FIELDS = frozenset(
    {"dataset", "gold_table", "root_source", "source_dataset", "source_system"}
)
_STATUS_FIELDS = frozenset(
    {
        "data_readiness",
        "data_status",
        "evaluation_status",
        "readiness_status",
        "source_status",
    }
)
_EVIDENCE_FIELDS = frozenset(
    {
        "analysis_evidence",
        "evidence",
        "evidence_pack",
        "evidence_refs",
    }
)
_VOLATILE_ATTESTATION_FIELDS = frozenset(
    {"attestation_key_id", "server_attestation", "source_row_hash"}
)


def _canonical(value: Any) -> str:
    return json.dumps(value, default=str, separators=(",", ":"), sort_keys=True)


def _identity_value(item: Mapping[str, Any], *keys: str) -> str:
    metadata = item.get("metadata")
    for source in (item, metadata if isinstance(metadata, Mapping) else {}):
        for key in keys:
            value = str(source.get(key) or "").strip()
            if value:
                return value
    return ""


def _stable_evidence(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _stable_evidence(nested)
            for key, nested in sorted(value.items(), key=lambda entry: str(entry[0]))
            if str(key) not in _VOLATILE_ATTESTATION_FIELDS
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        stable = [_stable_evidence(entry) for entry in value]
        return sorted(stable, key=_canonical)
    return value


def _semantic_values(item: Mapping[str, Any]) -> dict[str, list[str]]:
    values: dict[str, set[str]] = {}
    excluded = _IDENTITY_FIELDS | _ROOT_FIELDS | _STATUS_FIELDS
    for surface in semantic_surfaces(item):
        for key in POLICY_FIELDS - excluded:
            if key not in surface or surface[key] in (None, ""):
                continue
            value = (
                _stable_evidence(surface[key])
                if key in _EVIDENCE_FIELDS
                else surface[key]
            )
            values.setdefault(key, set()).add(_canonical(value))
    return {key: sorted(entries) for key, entries in sorted(values.items())}


def _root_values(item: Mapping[str, Any]) -> list[str]:
    values = {
        str(surface[key]).strip()
        for surface in semantic_surfaces(item)
        for key in _ROOT_FIELDS
        if key in surface and str(surface[key] or "").strip()
    }
    return sorted(values)


def business_observation_fingerprint(item: Mapping[str, Any]) -> str:
    refs = parent_references(item)
    payload = {
        "item_id": _identity_value(item, "id", "item_id"),
        "cartridge_id": _identity_value(item, "cartridge", "cartridge_id"),
        "kind": _identity_value(item, "kind", "item_kind").lower(),
        "observation": _semantic_values(item),
        "lineage": sorted(refs.ids),
        "root": _root_values(item),
    }
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


__all__ = ("business_observation_fingerprint",)
