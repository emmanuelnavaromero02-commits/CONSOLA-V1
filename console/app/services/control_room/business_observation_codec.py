from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any


ENVELOPE_KEY = "business_observation"
ENVELOPE_VERSION = 1
INVALID_ENVELOPE_FIELD = "_business_observation_invalid"
POLICY_FIELDS = frozenset(
    {
        INVALID_ENVELOPE_FIELD,
        "actual_value",
        "affected_count",
        "aggregation_type",
        "analysis_evidence",
        "as_of",
        "count",
        "data_readiness",
        "data_status",
        "dataset",
        "denominator",
        "denominator_count",
        "derived_from",
        "detected_at",
        "evaluation_status",
        "evidence",
        "evidence_id",
        "evidence_pack",
        "evidence_pack_id",
        "evidence_refs",
        "gold_table",
        "is_observed",
        "item_kind",
        "kind",
        "lineage",
        "metric_kind",
        "metric",
        "metric_name",
        "metric_type",
        "metric_value",
        "measure_name",
        "indicator_id",
        "numerator",
        "numerator_count",
        "observation_date",
        "observation_valid",
        "observed_at",
        "observed_value",
        "parent_item_id",
        "population",
        "population_count",
        "readiness_status",
        "sample_count",
        "source_dataset",
        "source_item_id",
        "source_row_count",
        "source_status",
        "source_system",
        "total_count",
        "value",
        "value_observed",
        "value_type",
    }
)
_STRUCTURED_POLICY_FIELDS = frozenset(
    {
        "analysis_evidence",
        "derived_from",
        "evidence",
        "evidence_pack",
        "evidence_refs",
        "lineage",
    }
)
LEGACY_FLAT_ENVELOPE_FIELDS = POLICY_FIELDS
EXPLICIT_SEMANTIC_SURFACE_PATHS = (
    (),
    ("details",),
    ("metadata",),
    ("metadata", "details"),
    ("observation",),
    ("metadata", "observation"),
    ("intelligence",),
    ("intelligence", "signal"),
    ("metadata", "intelligence"),
    ("metadata", "intelligence", "signal"),
)
METADATA_SEMANTIC_SURFACE_PATHS = tuple(
    path[1:]
    for path in EXPLICIT_SEMANTIC_SURFACE_PATHS
    if path and path[0] == "metadata"
)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _surface_at_path(
    item: Mapping[str, Any], path: tuple[str, ...]
) -> Mapping[str, Any]:
    surface = item
    for key in path:
        surface = _mapping(surface.get(key))
    return surface


def _explicit_surfaces(item: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    return tuple(
        _surface_at_path(item, path) for path in EXPLICIT_SEMANTIC_SURFACE_PATHS
    )


def _claim(surface: Mapping[str, Any]) -> dict[str, Any]:
    return {key: surface[key] for key in POLICY_FIELDS if key in surface}


def _claim_key(value: Mapping[str, Any]) -> str:
    return json.dumps(dict(value), default=str, separators=(",", ":"), sort_keys=True)


def _valid_claim(claim: Mapping[str, Any]) -> bool:
    if not claim or any(key not in POLICY_FIELDS for key in claim):
        return False
    for key, value in claim.items():
        if key == INVALID_ENVELOPE_FIELD:
            if value is not True:
                return False
        elif key == "lineage":
            if not isinstance(value, Mapping):
                return False
        elif key in _STRUCTURED_POLICY_FIELDS:
            if isinstance(value, (bytes, bytearray, set, frozenset)):
                return False
        elif isinstance(value, (Mapping, list, tuple, set, frozenset)):
            return False
    return True


def _legacy_flat_claim(envelope: Mapping[str, Any]) -> Mapping[str, Any] | None:
    if not envelope or any(key not in LEGACY_FLAT_ENVELOPE_FIELDS for key in envelope):
        return None
    claim = _claim(envelope)
    return claim if _valid_claim(claim) else None


def persisted_claims(item: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    claims: list[Mapping[str, Any]] = []
    for container in (item, _mapping(item.get("metadata"))):
        if ENVELOPE_KEY not in container:
            continue
        envelope = container.get(ENVELOPE_KEY)
        if not isinstance(envelope, Mapping):
            claims.append({INVALID_ENVELOPE_FIELD: True})
            continue
        if "version" not in envelope:
            legacy = _legacy_flat_claim(envelope)
            if legacy is not None:
                claims.append(legacy)
            else:
                claims.append({INVALID_ENVELOPE_FIELD: True})
            continue
        raw_claims = envelope.get("claims")
        if (
            type(envelope.get("version")) is not int
            or envelope.get("version") != ENVELOPE_VERSION
            or set(envelope) != {"version", "claims"}
            or not isinstance(raw_claims, list)
        ):
            claims.append({INVALID_ENVELOPE_FIELD: True})
            continue
        if any(
            not isinstance(value, Mapping) or not _valid_claim(value)
            for value in raw_claims
        ):
            claims.append({INVALID_ENVELOPE_FIELD: True})
            continue
        claims.extend(raw_claims)
    return tuple(claims)


def semantic_surfaces(item: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    return (*_explicit_surfaces(item), *persisted_claims(item))


def observation_envelope(item: Mapping[str, Any]) -> dict[str, Any]:
    claims = [*persisted_claims(item)]
    claims.extend(
        claim for surface in _explicit_surfaces(item) if (claim := _claim(surface))
    )
    unique = {_claim_key(claim): dict(claim) for claim in claims}
    return {
        "version": ENVELOPE_VERSION,
        "claims": [unique[key] for key in sorted(unique)],
    }


def with_observation_envelope(
    metadata: Mapping[str, Any],
    item: Mapping[str, Any],
) -> dict[str, Any]:
    return {**dict(metadata), ENVELOPE_KEY: observation_envelope(item)}


def nonempty_mapping_fields(
    item: Mapping[str, Any],
    keys: tuple[str, ...],
) -> dict[str, Any]:
    return {
        key: dict(value)
        for key in keys
        if isinstance((value := item.get(key)), Mapping) and value
    }


__all__ = (
    "ENVELOPE_KEY",
    "ENVELOPE_VERSION",
    "EXPLICIT_SEMANTIC_SURFACE_PATHS",
    "INVALID_ENVELOPE_FIELD",
    "LEGACY_FLAT_ENVELOPE_FIELDS",
    "METADATA_SEMANTIC_SURFACE_PATHS",
    "nonempty_mapping_fields",
    "observation_envelope",
    "persisted_claims",
    "semantic_surfaces",
    "with_observation_envelope",
)
