from __future__ import annotations

from collections.abc import Mapping
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


def _token(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def runtime_reference_matches_item(
    reference: Mapping[str, Any], item: Mapping[str, Any]
) -> bool:
    locator = reference.get("source_locator")
    if not isinstance(locator, Mapping):
        return False
    field = str(locator.get("field") or "").strip()
    value = _token(locator.get("value"))
    if not field or not value:
        return False
    surfaces = semantic_maps(item)
    if any(
        field in surface and _token(surface.get(field)) == value for surface in surfaces
    ):
        return True
    identities = {
        _token(surface.get(identity_field))
        for surface in surfaces
        for identity_field in _BUSINESS_IDENTITY_FIELDS
        if _token(surface.get(identity_field))
    }
    return value in identities


__all__ = ("runtime_reference_matches_item",)
