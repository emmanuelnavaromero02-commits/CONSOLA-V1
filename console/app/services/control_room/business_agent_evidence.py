"""Mission 5: agent-authored rows cannot carry server attestations.

Control Room decides that a persisted item is backed by evidence by looking for
signed runtime references on the item's semantic surfaces, and the metadata of a
row mcp-infra wrote on an agent's behalf is one of those surfaces. Until now an
agent could put a signed reference it had read somewhere else into
``evidence_refs`` and the alert would read as attested.

mcp-infra now refuses attestation-bearing arguments at write time. This is the
read-side half, so a row written before that check, or through any other path
into the column, cannot pass a replayed signature off as evidence. Attested
evidence for a scheduled monitor alert is resolved separately, from the tickets
console minted itself (``evidence_tickets``).

Which rows count as agent-authored is decided first by the ``item_id``, which
mcp-infra derives server-side (``agent_alert:`` + 32 hex) and no caller chooses,
and only then by metadata markers, which a writer of the column could remove.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

ATTESTATION_MARKER = "server_attestation"
AGENT_ALERT_ITEM_ID = re.compile(r"^agent_alert:[0-9a-f]{32}$")
_MAX_DEPTH = 16
_DROP = object()


def is_agent_authored(metadata: Mapping[str, Any], *, item_id: Any = None) -> bool:
    """True for rows ``control_room__raise_alert`` wrote."""
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
    """Metadata with every attestation-bearing mapping removed, for agent rows.

    Rows console persisted itself are returned unchanged.
    """
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
