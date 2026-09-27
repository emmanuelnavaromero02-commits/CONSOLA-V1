from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from app.schemas.control_room_direct_actions import (
    ExceptionApprovalResponse,
    ExceptionReopenResponse,
)
from app.services import audit_service, auth, control_room_service
from app.services.control_room.business_action_authority_policy import (
    APPROVE_EXCEPTION_TEMPLATE_ID,
    REOPEN_EXCEPTION_TEMPLATE_ID,
    actor_id,
)
from app.services.control_room.business_action_direct_contract import (
    EXCEPTION_RESOLUTION,
)
from app.services.control_room.business_action_handle import (
    resolve_business_action_handle,
)
from app.services.control_room.business_action_mutations import (
    persist_status_transition,
    require_exact_count,
)
from app.services.control_room.business_direct_action_authority import (
    LockedDirectAction,
    changed_error,
    consume_direct_action,
    find_direct_action_replay,
    lock_direct_action,
)
from app.services.db_scope import run_with_db_scope


EXCEPTION_REOPENED = "exception_reopened"
RESOLUTION_SQL = """
UPDATE control_room_items
   SET metadata = COALESCE(metadata, '{}'::jsonb) || $1::jsonb
 WHERE workspace_id = $2
   AND item_id = $3
   AND owner_user_id IS NOT DISTINCT FROM $4
   AND status = 'dismissed'
"""


def _transition_item(locked: LockedDirectAction) -> dict[str, Any]:
    return {
        "id": locked.contract.item_id,
        "owner_user_id": locked.row.get("owner_user_id"),
    }


async def _audit(
    conn: Any,
    *,
    user: Mapping[str, Any],
    action: str,
    item_id: str,
    template_id: str,
    reason: str,
    ip: str | None,
    user_agent: str | None,
) -> None:
    await audit_service.record_event(
        connection=conn,
        user_id=user.get("id"),
        email=user.get("email"),
        action=action,
        resource_type="control_room_item",
        resource_id=item_id,
        ip=ip,
        user_agent=user_agent,
        status="success",
        metadata={"template_id": template_id, "reason": reason},
        critical=True,
    )


async def approve_exception(
    user: Mapping[str, Any],
    *,
    action_handle: str,
    reason: str,
    idempotency_key: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> ExceptionApprovalResponse:
    replay = await find_direct_action_replay(
        user,
        action_handle=action_handle,
        template_id=APPROVE_EXCEPTION_TEMPLATE_ID,
        idempotency_key=idempotency_key,
    )
    if replay is not None:
        if replay.result_state != EXCEPTION_RESOLUTION:
            raise changed_error()
        return ExceptionApprovalResponse(action_handle=action_handle)
    resolved = await resolve_business_action_handle(
        user,
        action_handle,
        allowed_template_ids=frozenset({APPROVE_EXCEPTION_TEMPLATE_ID}),
    )
    approved_at = datetime.now(UTC).isoformat()
    pool = await auth.pool()

    async def _write(conn: Any, _tenant_id: str | None, workspace_id: str) -> None:
        locked = await lock_direct_action(
            conn,
            user=user,
            action_handle=action_handle,
            item_id=resolved.item_id,
            template_id=APPROVE_EXCEPTION_TEMPLATE_ID,
        )
        item = _transition_item(locked)
        resolution = {
            "resolution": EXCEPTION_RESOLUTION,
            "resolution_actor_id": actor_id(user),
            "resolution_reason": reason,
            "resolution_at": approved_at,
            "resolution_observation_fingerprint": (
                locked.contract.observation_fingerprint
            ),
            "resolution_evidence_digest": locked.contract.evidence_digest,
        }
        await persist_status_transition(
            conn,
            user=user,
            item=item,
            workspace_id=workspace_id,
            target_status="dismissed",
            event_type=EXCEPTION_RESOLUTION,
            reason=reason,
            ensure_item_row=control_room_service._ensure_item_row,
        )
        result = await conn.execute(
            RESOLUTION_SQL,
            json.dumps(resolution),
            workspace_id,
            item["id"],
            item["owner_user_id"],
        )
        require_exact_count(result, "UPDATE")
        await _audit(
            conn,
            user=user,
            action="control_room.exception.approve",
            item_id=item["id"],
            template_id=APPROVE_EXCEPTION_TEMPLATE_ID,
            reason=reason,
            ip=ip,
            user_agent=user_agent,
        )
        await consume_direct_action(
            conn,
            locked,
            user=user,
            idempotency_key=idempotency_key,
            result_state=EXCEPTION_RESOLUTION,
        )

    await run_with_db_scope(pool, dict(user), _write)
    return ExceptionApprovalResponse(action_handle=action_handle)


async def reopen_exception(
    user: Mapping[str, Any],
    *,
    action_handle: str,
    reason: str,
    idempotency_key: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> ExceptionReopenResponse:
    replay = await find_direct_action_replay(
        user,
        action_handle=action_handle,
        template_id=REOPEN_EXCEPTION_TEMPLATE_ID,
        idempotency_key=idempotency_key,
    )
    if replay is not None:
        if replay.result_state != EXCEPTION_REOPENED:
            raise changed_error()
        return ExceptionReopenResponse(action_handle=action_handle)
    resolved = await resolve_business_action_handle(
        user,
        action_handle,
        allowed_template_ids=frozenset({REOPEN_EXCEPTION_TEMPLATE_ID}),
    )
    pool = await auth.pool()

    async def _write(conn: Any, _tenant_id: str | None, workspace_id: str) -> None:
        locked = await lock_direct_action(
            conn,
            user=user,
            action_handle=action_handle,
            item_id=resolved.item_id,
            template_id=REOPEN_EXCEPTION_TEMPLATE_ID,
        )
        item = _transition_item(locked)
        await persist_status_transition(
            conn,
            user=user,
            item=item,
            workspace_id=workspace_id,
            target_status="open",
            event_type=EXCEPTION_REOPENED,
            reason=reason,
            ensure_item_row=control_room_service._ensure_item_row,
        )
        await _audit(
            conn,
            user=user,
            action="control_room.exception.reopen",
            item_id=item["id"],
            template_id=REOPEN_EXCEPTION_TEMPLATE_ID,
            reason=reason,
            ip=ip,
            user_agent=user_agent,
        )
        await consume_direct_action(
            conn,
            locked,
            user=user,
            idempotency_key=idempotency_key,
            result_state=EXCEPTION_REOPENED,
        )

    await run_with_db_scope(pool, dict(user), _write)
    return ExceptionReopenResponse(action_handle=action_handle)


__all__ = (
    "EXCEPTION_REOPENED",
    "RESOLUTION_SQL",
    "approve_exception",
    "reopen_exception",
)
