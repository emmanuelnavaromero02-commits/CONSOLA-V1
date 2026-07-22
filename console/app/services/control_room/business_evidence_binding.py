from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

from app.services.control_room.business_semantic_slots import semantic_maps


_BUSINESS_IDENTITY_FIELDS = (
    "id",
    "item_id",
    "entity_id",
    "anomaly_id",
    "signal_id",
    "action_id",
    "source_item_id",
)
_KIND_FIELDS = ("kind", "item_kind", "anomaly_type", "action_type")
_METRIC_NAME_FIELDS = ("metric_name", "metric", "measure_name", "indicator_id")
_METRIC_TYPE_FIELDS = ("metric_type", "value_type")
_VALUE_FIELDS = (
    "observed_value",
    "actual_value",
    "value_decimal",
    "count",
    "affected_count",
    "amount",
    "value",
)
_TIME_FIELDS = (
    "observation_date",
    "detected_at",
    "generated_at",
    "freshness_at",
    "period_key",
    "as_of",
    "observed_at",
)
_BINDING_VERSION = "business-observation-v1"


def _token(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _number_token(value: Any) -> str:
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return _token(value)
    if not number.is_finite():
        return _token(value)
    normalized = format(number.normalize(), "f")
    return "0" if normalized in {"-0", ""} else normalized


def _first(surfaces: tuple[Mapping[str, Any], ...], fields: tuple[str, ...]):
    for field in fields:
        for surface in surfaces:
            if field in surface and surface.get(field) is not None:
                value = surface.get(field)
                if str(value).strip():
                    return field, value
    return None, None


def _binding_fingerprint(binding: Mapping[str, Any]) -> str:
    payload = json.dumps(
        {
            "version": binding.get("version"),
            "identity": binding.get("identity"),
            "observation": binding.get("observation"),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def runtime_business_binding(
    source_row: Mapping[str, Any], *, locator_field: str, observed_at: str
) -> dict[str, Any] | None:
    surfaces = tuple(semantic_maps(source_row))
    identity_field, identity_value = _first(surfaces, _BUSINESS_IDENTITY_FIELDS)
    if not identity_value:
        identity_field, identity_value = locator_field, source_row.get(locator_field)
    identity = _token(identity_value)
    if not identity_field or not identity:
        return None
    observation: dict[str, str] = {}
    for name, fields in (
        ("kind", _KIND_FIELDS),
        ("metric_name", _METRIC_NAME_FIELDS),
        ("metric_type", _METRIC_TYPE_FIELDS),
    ):
        _field, value = _first(surfaces, fields)
        if value is not None:
            observation[name] = _token(value)
    _field, value = _first(surfaces, _VALUE_FIELDS)
    if value is not None:
        observation["value"] = _number_token(value)
    observation["observed_at"] = _token(observed_at)
    binding: dict[str, Any] = {
        "version": _BINDING_VERSION,
        "identity": {"field": str(identity_field), "value": identity},
        "observation": observation,
    }
    binding["fingerprint"] = _binding_fingerprint(binding)
    return binding


def _surface_values(item: Mapping[str, Any], fields: tuple[str, ...]) -> set[str]:
    return {
        _token(surface.get(field))
        for surface in semantic_maps(item)
        for field in fields
        if surface.get(field) is not None and _token(surface.get(field))
    }


def _observation_matches(binding: Mapping[str, Any], item: Mapping[str, Any]) -> bool:
    observation = binding.get("observation")
    if not isinstance(observation, Mapping):
        return False
    checks = (
        ("kind", _KIND_FIELDS, _token, False),
        ("metric_name", _METRIC_NAME_FIELDS, _token, True),
        ("metric_type", _METRIC_TYPE_FIELDS, _token, True),
        ("value", _VALUE_FIELDS, _number_token, True),
    )
    surfaces = semantic_maps(item)
    for claim, fields, normalizer, required_when_declared in checks:
        expected = str(observation.get(claim) or "")
        actual = {
            normalizer(surface.get(field))
            for surface in surfaces
            for field in fields
            if surface.get(field) is not None
        }
        if required_when_declared and actual and not expected:
            return False
        if expected and expected not in actual:
            return False
    observed_at = str(observation.get("observed_at") or "")
    if observed_at:
        actual = _surface_values(item, _TIME_FIELDS)
        if (
            actual
            and observed_at not in actual
            and observed_at[:10]
            not in {value[:10] for value in actual if len(value) >= 10}
        ):
            return False
    return True


def runtime_reference_matches_item(
    reference: Mapping[str, Any], item: Mapping[str, Any]
) -> bool:
    binding = reference.get("business_binding")
    if (
        not isinstance(binding, Mapping)
        or binding.get("version") != _BINDING_VERSION
        or binding.get("fingerprint") != _binding_fingerprint(binding)
    ):
        return False
    identity = binding.get("identity")
    if not isinstance(identity, Mapping):
        return False
    identity_field = str(identity.get("field") or "").strip()
    identity_value = _token(identity.get("value"))
    if not identity_field or not identity_value:
        return False
    locator = reference.get("source_locator")
    if not isinstance(locator, Mapping):
        return False
    field = str(locator.get("field") or "").strip()
    value = _token(locator.get("value"))
    if not field or not value:
        return False
    surfaces = semantic_maps(item)
    locator_matches = any(
        field in surface and _token(surface.get(field)) == value for surface in surfaces
    )
    identities = {
        _token(surface.get(identity_field))
        for surface in surfaces
        for identity_field in _BUSINESS_IDENTITY_FIELDS
        if _token(surface.get(identity_field))
    }
    locator_matches = locator_matches or value in identities
    exact_identity = any(
        identity_field in surface
        and _token(surface.get(identity_field)) == identity_value
        for surface in surfaces
    )
    business_identity = identity_value in identities
    return bool(
        locator_matches
        and (exact_identity or business_identity)
        and _observation_matches(binding, item)
    )


__all__ = ("runtime_business_binding", "runtime_reference_matches_item")
