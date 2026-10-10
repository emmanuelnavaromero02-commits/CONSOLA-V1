from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from typing import Any

from app.services.control_room.business_eligibility import (
    BUSINESS_MATERIALIZATION_FIELDS,
)
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
_UNORDERED_STRUCTURED_FIELDS = _EVIDENCE_FIELDS | frozenset({"derived_from", "lineage"})
_VOLATILE_ATTESTATION_FIELDS = frozenset(
    {"attestation_key_id", "server_attestation", "source_row_hash"}
)
_MATERIALIZATION_FIELDS = frozenset(BUSINESS_MATERIALIZATION_FIELDS)
_OBSERVATION_STAMP_FIELDS = frozenset({"detected_at", "observed_at"})
_BUSINESS_PERIOD_FIELDS = frozenset({"as_of", "observation_date", "period_key"})
_DERIVED_DIGEST_FIELDS = frozenset({"fingerprint"})
_MATERIALIZATION_TOKEN = "materialization_instant"


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


def observation_instant(value: Any) -> str:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime(value.year, value.month, value.day, tzinfo=UTC)
    else:
        raw = str(value or "").strip()
        if not raw:
            return ""
        try:
            if len(raw) == 10:
                parsed_date = date.fromisoformat(raw)
                parsed = datetime(
                    parsed_date.year,
                    parsed_date.month,
                    parsed_date.day,
                    tzinfo=UTC,
                )
            else:
                parsed = datetime.fromisoformat(
                    raw.replace("Z", "+00:00").replace("z", "+00:00")
                )
        except ValueError:
            return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return (
        parsed.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    )


def _materialization_instants(item: Mapping[str, Any]) -> frozenset[str]:
    return frozenset(
        instant
        for surface in semantic_surfaces(item)
        for key in _MATERIALIZATION_FIELDS
        if key in surface and (instant := observation_instant(surface[key]))
    )


def _restates_materialization(value: Any, instants: frozenset[str]) -> bool:
    return (
        bool(instants)
        and isinstance(value, (str, date))
        and observation_instant(value) in instants
    )


def _stable_structured_value(
    value: Any, instants: frozenset[str], *, period: bool = False
) -> tuple[Any, bool]:
    if isinstance(value, Mapping):
        stable: dict[str, Any] = {}
        masked = False
        for key, nested in sorted(value.items(), key=lambda entry: str(entry[0])):
            name = str(key)
            if name in _VOLATILE_ATTESTATION_FIELDS:
                continue
            if name in _MATERIALIZATION_FIELDS:
                masked = True
                continue
            stable[name], nested_masked = _stable_structured_value(
                nested, instants, period=name in _BUSINESS_PERIOD_FIELDS
            )
            masked = masked or nested_masked
        if masked:
            for name in _DERIVED_DIGEST_FIELDS:
                stable.pop(name, None)
        return stable, masked
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        entries = [
            _stable_structured_value(entry, instants, period=period) for entry in value
        ]
        return (
            sorted((entry for entry, _masked in entries), key=_canonical),
            any(masked for _entry, masked in entries),
        )
    if not period and _restates_materialization(value, instants):
        return _MATERIALIZATION_TOKEN, True
    return value, False


def _semantic_values(item: Mapping[str, Any]) -> dict[str, list[str]]:
    values: dict[str, set[str]] = {}
    excluded = _IDENTITY_FIELDS | _ROOT_FIELDS | _STATUS_FIELDS
    instants = _materialization_instants(item)
    for surface in semantic_surfaces(item):
        for key in POLICY_FIELDS - excluded:
            if key not in surface or surface[key] in (None, ""):
                continue
            if key in _UNORDERED_STRUCTURED_FIELDS:
                value, _masked = _stable_structured_value(surface[key], instants)
            elif key in _OBSERVATION_STAMP_FIELDS and _restates_materialization(
                surface[key], instants
            ):
                value = _MATERIALIZATION_TOKEN
            else:
                value = surface[key]
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


__all__ = ("business_observation_fingerprint", "observation_instant")
