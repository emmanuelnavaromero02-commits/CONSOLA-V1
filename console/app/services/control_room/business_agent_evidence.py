from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

ATTESTATION_MARKER = "server_attestation"
AGENT_ALERT_ITEM_ID = re.compile(r"^agent_alert:[0-9a-f]{32}$")
_MAX_DEPTH = 16
_DROP = object()


def is_agent_authored(metadata: Mapping[str, Any], *, item_id: Any = None) -> bool:
    if AGENT_ALERT_ITEM_ID.fullmatch(str(item_id or "").strip()):
        return True
    control_state = metadata.get("control_state")
    return (
        str(metadata.get("source") or "").strip().lower() == "agent"
        or bool(str(metadata.get("agent_id") or "").strip())
        or (
            isinstance(control_state, Mapping)
            and str(control_state.get("source") or "").strip().lower() == "agent"
        )
    )


def _strip(value: Any, depth: int) -> Any:
    if depth > _MAX_DEPTH:
        return _DROP
    if isinstance(value, Mapping):
        if ATTESTATION_MARKER in value:
            return _DROP
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            projected = _strip(item, depth + 1)
            if projected is not _DROP:
                cleaned[key] = projected
        return cleaned
    if isinstance(value, (list, tuple)):
        return [
            projected
            for item in value
            if (projected := _strip(item, depth + 1)) is not _DROP
        ]
    return value


def without_agent_attestations(
    metadata: Mapping[str, Any], *, item_id: Any = None
) -> dict[str, Any]:
    if not is_agent_authored(metadata, item_id=item_id):
        return dict(metadata)
    cleaned = _strip(metadata, 0)
    return cleaned if isinstance(cleaned, dict) else {}


__all__ = (
    "AGENT_ALERT_ITEM_ID",
    "ATTESTATION_MARKER",
    "is_agent_authored",
    "without_agent_attestations",
)
