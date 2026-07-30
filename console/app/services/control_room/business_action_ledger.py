from __future__ import annotations

from collections.abc import Mapping
import json
from typing import Any

from fastapi import HTTPException

from app.services.control_room.business_action_authorization_snapshot import (
    capture_authorization_snapshot,
)
from app.services.control_room.business_action_digest import action_contract_digest


def _permission_for_event(event_type: str, from_state: str) -> str:
    if event_type == "intent_created":
        return "control_room.write"
    if event_type == "stale":
        return (
            "control_room.execute"
            if from_state == "approved"
            else "control_room.approve"
        )
    if event_type in {"approval_claimed", "approved", "rejected"}:
        return "control_room.approve"
    if event_type in {"execution_reserved", "completed", "failed"}:
        return "control_room.execute"
    raise ValueError("control room authority event permission is invalid")


async def append_intent_event(
    conn: Any,
    *,
    intent: Mapping[str, Any],
    actor_user_id: int,
    event_type: str,
    from_state: str,
    to_state: str,
    intent_version: int,
    result_code: str,
    operation_digest: str,
) -> int:
    tenant_id = str(intent["tenant_id"])
    workspace_id = str(intent["workspace_id"])
    intent_id = str(intent["id"])
    permission = _permission_for_event(event_type, from_state)
    authorization = await capture_authorization_snapshot(
        conn,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        permission=permission,
    )
    if authorization is None:
        raise HTTPException(403, "action authority is unavailable")
    authorization_digest = action_contract_digest(
        {
            "version": "control-room-authorized-event/v1",
            "intent_id": intent_id,
            "intent_version": int(intent_version),
            "event_type": event_type,
            "actor_user_id": int(actor_user_id),
            "authorization": authorization.snapshot,
        }
    )
    row = await conn.fetchrow(
        """
        INSERT INTO control_room_action_intent_events (
            tenant_id, workspace_id, intent_id, actor_user_id, event_type,
            from_state, to_state, intent_version, result_code,
            correlation_id, operation_digest, authorization_permission,
            actor_global_role, actor_workspace_role,
            authorization_grant_source, access_revision_digest,
            rbac_policy_digest, authorization_snapshot, authorization_digest
        ) VALUES (
            $1::uuid, $2::uuid, $3::uuid, $4, $5,
            $6, $7, $8, $9, $10::uuid, $11, $12,
            $13, $14, $15, $16, $17, $18::jsonb, $19
        )
        RETURNING id
        """,
        tenant_id,
        workspace_id,
        intent_id,
        int(actor_user_id),
        event_type,
        from_state,
        to_state,
        int(intent_version),
        result_code,
        str(intent["correlation_id"]),
        operation_digest,
        authorization.permission,
        authorization.global_role,
        authorization.workspace_role,
        authorization.grant_source,
        authorization.access_revision_digest,
        authorization.rbac_policy_digest,
        json.dumps(authorization.snapshot, sort_keys=True, separators=(",", ":")),
        authorization_digest,
    )
    if not row:
        raise RuntimeError("control room authority ledger write failed")
    return int(row["id"])


__all__ = ("append_intent_event",)
