from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException

from app.services.control_room.business_action_ledger import append_intent_event
from app.services.control_room.business_action_revalidation import (
    actor_has_current_permission,
    revalidate_intent,
)
from app.services.control_room.business_action_tokens import server_operation_digest


_TRANSITIONS = {
    "approval_claimed": ("pending_approval", "pending_approval", "claimed"),
    "approved": ("pending_approval", "approved", "approved"),
    "rejected": ("pending_approval", "rejected", "rejected"),
    "stale": (("pending_approval", "approved"), "stale", "stale"),
    "execution_reserved": ("approved", "execution_reserved", "execution_reserved"),
    "completed": ("execution_reserved", "completed", "completed"),
    "failed": ("execution_reserved", "failed", "failed"),
}


async def lock_intent(
    conn: Any,
    *,
    tenant_id: str,
    workspace_id: str,
    intent_id: str,
) -> dict[str, Any]:
    row = await conn.fetchrow(
        """
        SELECT *, id::text AS id, tenant_id::text AS tenant_id,
               workspace_id::text AS workspace_id,
               correlation_id::text AS correlation_id
          FROM control_room_action_intents
         WHERE tenant_id = $1::uuid AND workspace_id = $2::uuid
           AND id = $3::uuid AND expires_at > NOW()
         FOR UPDATE
        """,
        tenant_id,
        workspace_id,
        intent_id,
    )
    if not row:
        raise HTTPException(404, "action authority not found")
    return dict(row)


async def intent_authority_is_current(conn: Any, intent: Mapping[str, Any]) -> bool:
    maker_current = await actor_has_current_permission(
        conn,
        tenant_id=str(intent["tenant_id"]),
        workspace_id=str(intent["workspace_id"]),
        user_id=int(intent["maker_user_id"]),
        permission="control_room.write",
    )
    return maker_current and await revalidate_intent(conn, intent) is not None


def transition_operation_digest(
    intent: Mapping[str, Any], *, operation: str, actor_user_id: int
) -> str:
    return server_operation_digest(
        workspace_id=str(intent["workspace_id"]),
        intent_id=str(intent["id"]),
        operation=operation,
        contract_digest=str(intent["contract_digest"]),
        actor_user_id=actor_user_id,
    )


async def transition_intent(
    conn: Any,
    *,
    intent: Mapping[str, Any],
    actor_user_id: int,
    event_type: str,
    operation_digest: str,
    checker_user_id: int | None = None,
    executor_user_id: int | None = None,
) -> dict[str, Any]:
    if event_type not in _TRANSITIONS:
        raise ValueError("control room authority transition is invalid")
    expected, next_state, result_code = _TRANSITIONS[event_type]
    from_state = str(intent.get("state") or "")
    allowed = (expected,) if isinstance(expected, str) else expected
    if allowed is not None and from_state not in allowed:
        raise HTTPException(404, "action authority not found")
    next_version = int(intent.get("state_version") or 0) + 1
    row = await conn.fetchrow(
        """
        UPDATE control_room_action_intents
           SET state = $1, state_version = $2, result_code = $3,
               checker_user_id = COALESCE($4, checker_user_id),
               executor_user_id = COALESCE($5, executor_user_id),
               updated_at = NOW()
         WHERE tenant_id = $6::uuid AND workspace_id = $7::uuid
           AND id = $8::uuid AND state = $9 AND state_version = $10
         RETURNING *, id::text AS id, tenant_id::text AS tenant_id,
                     workspace_id::text AS workspace_id,
                     correlation_id::text AS correlation_id
        """,
        next_state,
        next_version,
        result_code,
        checker_user_id,
        executor_user_id,
        str(intent["tenant_id"]),
        str(intent["workspace_id"]),
        str(intent["id"]),
        from_state,
        int(intent["state_version"]),
    )
    if not row:
        raise HTTPException(404, "action authority not found")
    updated = dict(row)
    await append_intent_event(
        conn,
        intent=updated,
        actor_user_id=actor_user_id,
        event_type=event_type,
        from_state=from_state,
        to_state=next_state,
        intent_version=next_version,
        result_code=result_code,
        operation_digest=operation_digest,
    )
    return updated


__all__ = (
    "intent_authority_is_current",
    "lock_intent",
    "transition_intent",
    "transition_operation_digest",
)
