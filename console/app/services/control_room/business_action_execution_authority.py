from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from fastapi import HTTPException

from app.services import auth
from app.services.control_room.business_action_authority_policy import (
    actor_id,
    authority_scope,
    require_distinct_actors,
    require_execute,
)
from app.services.control_room.business_action_revalidation import (
    actor_has_current_permission,
)
from app.services.control_room.business_action_tokens import (
    claim_stage_token,
    consume_stage_token,
    issue_stage_token,
)
from app.services.control_room.business_action_transition_core import (
    intent_authority_is_current,
    lock_intent,
    transition_intent,
    transition_operation_digest,
)
from app.services.control_room.business_action_transitions import TransitionResult
from app.services.db_scope import run_with_db_scope


@dataclass(frozen=True)
class ExecutionClaim:
    intent_id: str = field(repr=False)
    state: str
    state_version: int
    expires_at: datetime
    execution_handle: str | None = field(repr=False)


async def _execute_permission_current(conn: Any, user: Mapping[str, Any]) -> bool:
    tenant_id, workspace_id = authority_scope(user)
    return await actor_has_current_permission(
        conn,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=actor_id(user),
        permission="control_room.execute",
    )


async def issue_execution_handle(
    user: Mapping[str, Any], intent_id: str
) -> ExecutionClaim:
    require_execute(user)
    tenant_id, workspace_id = authority_scope(user)
    executor_user_id = actor_id(user)
    pool = await auth.pool()

    async def _issue(
        conn: Any, scoped_tenant: str | None, scoped_workspace: str
    ) -> ExecutionClaim:
        if scoped_tenant != tenant_id or scoped_workspace != workspace_id:
            raise HTTPException(404, "action authority not found")
        intent = await lock_intent(
            conn,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            intent_id=intent_id,
        )
        require_distinct_actors(int(intent["maker_user_id"]), executor_user_id)
        if (
            str(intent["state"]) != "approved"
            or int(intent.get("checker_user_id") or 0) != executor_user_id
        ):
            raise HTTPException(404, "action authority not found")
        if not await _execute_permission_current(conn, user):
            raise HTTPException(403, "action authority is unavailable")
        if not await intent_authority_is_current(conn, intent):
            operation_digest = transition_operation_digest(
                intent,
                operation="issue_execution_stale",
                actor_user_id=executor_user_id,
            )
            stale = await transition_intent(
                conn,
                intent=intent,
                actor_user_id=executor_user_id,
                event_type="stale",
                operation_digest=operation_digest,
            )
            return ExecutionClaim(
                str(stale["id"]),
                "stale",
                int(stale["state_version"]),
                stale["expires_at"],
                None,
            )
        token = await issue_stage_token(
            conn,
            user=user,
            intent_id=intent_id,
            stage="execution",
            intent_expires_at=intent["expires_at"],
        )
        return ExecutionClaim(
            intent_id,
            "approved",
            int(intent["state_version"]),
            intent["expires_at"],
            token.handle,
        )

    return await run_with_db_scope(pool, dict(user), _issue)


async def reserve_execution(
    user: Mapping[str, Any], execution_handle: str
) -> TransitionResult:
    require_execute(user)
    tenant_id, workspace_id = authority_scope(user)
    executor_user_id = actor_id(user)
    pool = await auth.pool()

    async def _reserve(
        conn: Any, scoped_tenant: str | None, scoped_workspace: str
    ) -> TransitionResult:
        if scoped_tenant != tenant_id or scoped_workspace != workspace_id:
            raise HTTPException(404, "action authority not found")
        token = await claim_stage_token(
            conn,
            user=user,
            handle=execution_handle,
            stage="execution",
            operation="reserve_execution",
        )
        if token.replay:
            return TransitionResult(
                token.intent_id,
                str(token.result_state),
                int(token.result_version or 0),
                True,
            )
        intent = await lock_intent(
            conn,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            intent_id=token.intent_id,
        )
        require_distinct_actors(int(intent["maker_user_id"]), executor_user_id)
        if int(intent.get("checker_user_id") or 0) != executor_user_id:
            raise HTTPException(404, "action authority not found")
        if not await _execute_permission_current(conn, user):
            raise HTTPException(403, "action authority is unavailable")
        event_type = (
            "execution_reserved"
            if await intent_authority_is_current(conn, intent)
            else "stale"
        )
        updated = await transition_intent(
            conn,
            intent=intent,
            actor_user_id=executor_user_id,
            event_type=event_type,
            operation_digest=token.operation_digest,
            executor_user_id=(
                executor_user_id if event_type == "execution_reserved" else None
            ),
        )
        await consume_stage_token(
            conn,
            token=token,
            user=user,
            result_state=str(updated["state"]),
            result_version=int(updated["state_version"]),
        )
        return TransitionResult(
            str(updated["id"]),
            str(updated["state"]),
            int(updated["state_version"]),
        )

    return await run_with_db_scope(pool, dict(user), _reserve)


__all__ = ("ExecutionClaim", "issue_execution_handle", "reserve_execution")
