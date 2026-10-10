from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException

from app.domains.decisions.business_visibility import ORPHANED_KEY
from app.domains.decisions.council_protection import PROTECTED_DECISION_MESSAGE
from app.domains.decisions.payloads import DECISION_UPDATE_FIELDS
from app.services import audit_service


ORPHAN_MUTABLE_FIELDS = frozenset({"status", "outcome"})
ORPHAN_EDIT_MESSAGE = (
    "Esta decisión ya no tiene un ítem vigente del Control Room; "
    "solo puede cerrarse o reabrirse."
)
STATUS_AUDIT_ACTIONS = {"closed": "decision.close", "open": "decision.reopen"}
STATUS_AUDIT_FALLBACK = "decision.status.update"
OUTCOME_AUDIT_ACTION = "decision.outcome.update"


def is_orphaned(row: Mapping[str, Any] | None) -> bool:
    return bool(row) and row.get(ORPHANED_KEY) is True


def require_orphan_update_allowed(
    existing: Mapping[str, Any], body: Mapping[str, Any]
) -> None:
    requested = set(body) & DECISION_UPDATE_FIELDS
    if is_orphaned(existing) and requested - ORPHAN_MUTABLE_FIELDS:
        raise HTTPException(409, ORPHAN_EDIT_MESSAGE)


def require_orphan_not_deleted(existing: Mapping[str, Any]) -> None:
    if is_orphaned(existing):
        raise HTTPException(409, PROTECTED_DECISION_MESSAGE)


async def record_decision_status_change(
    conn: Any,
    *,
    user: Mapping[str, Any],
    existing: Mapping[str, Any],
    updated: Mapping[str, Any],
) -> None:
    before = str(existing.get("status") or "")
    after = str(updated.get("status") or "")
    if before == after:
        if existing.get("outcome") == updated.get("outcome"):
            return
        action = OUTCOME_AUDIT_ACTION
    else:
        action = STATUS_AUDIT_ACTIONS.get(after, STATUS_AUDIT_FALLBACK)
    await audit_service.record_event(
        connection=conn,
        user_id=user.get("id"),
        email=user.get("email"),
        action=action,
        resource_type="decision",
        resource_id=str(updated.get("id")),
        status="success",
        metadata={
            "workspace_id": str(updated.get("workspace_id") or ""),
            "from_status": before,
            "to_status": after,
            "from_outcome": existing.get("outcome"),
            "outcome": updated.get("outcome"),
            "control_room_orphan": is_orphaned(existing),
        },
        critical=True,
    )


__all__ = (
    "ORPHAN_EDIT_MESSAGE",
    "ORPHAN_MUTABLE_FIELDS",
    "OUTCOME_AUDIT_ACTION",
    "STATUS_AUDIT_ACTIONS",
    "is_orphaned",
    "record_decision_status_change",
    "require_orphan_not_deleted",
    "require_orphan_update_allowed",
)
