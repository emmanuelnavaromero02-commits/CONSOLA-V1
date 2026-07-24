from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any


CONTROL_ROOM_ORIGIN = "control_room"


def _kpi_list(value: Any) -> list[Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
    return list(value) if isinstance(value, list) else []


def is_reserved_control_room_provenance(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    provenance = value.get("provenance")
    if not isinstance(provenance, Mapping):
        return False
    return str(provenance.get("origin") or "").strip().lower() == CONTROL_ROOM_ORIGIN


def is_reserved_decision_provenance(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    provenance = value.get("provenance")
    if not isinstance(provenance, Mapping):
        return False
    marker_type = str(provenance.get("type") or "").strip().lower()
    return marker_type == "decision_provenance" or is_reserved_control_room_provenance(
        value
    )


def strip_control_room_provenance(value: Any) -> list[Any]:
    return [
        entry
        for entry in _kpi_list(value)
        if not is_reserved_decision_provenance(entry)
    ]


def decision_provenance(
    origin: str,
    *,
    item_id: str | None = None,
) -> dict[str, Any]:
    provenance: dict[str, Any] = {
        "type": "decision_provenance",
        "version": 1,
        "origin": str(origin).strip().lower(),
    }
    if item_id:
        provenance["item_id"] = str(item_id)
    return {
        "label": "Provenance",
        "value": provenance["origin"],
        "provenance": provenance,
    }


def decision_kpis_with_provenance(
    value: Any,
    origin: str,
    *,
    item_id: str | None = None,
) -> list[Any]:
    kpis = strip_control_room_provenance(value)
    kpis.append(decision_provenance(origin, item_id=item_id))
    return kpis


__all__ = (
    "CONTROL_ROOM_ORIGIN",
    "decision_kpis_with_provenance",
    "decision_provenance",
    "is_reserved_control_room_provenance",
    "is_reserved_decision_provenance",
    "strip_control_room_provenance",
)
