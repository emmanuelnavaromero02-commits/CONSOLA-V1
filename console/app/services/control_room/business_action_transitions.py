from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from fastapi import HTTPException

from app.services import auth
from app.services.control_room.business_action_authority_policy import (
    actor_id,
    authority_scope,
    require_approve,
    require_distinct_actors,
)
from app.services.control_room.business_action_revalidation import (
    actor_has_current_permission,
)
from app.services.control_room.business_action_tokens import (
    claim_stage_token,
    consume_stage_token,
    issue_stage_token,
    peek_stage_token_intent,
)
from app.services.control_room.business_action_transition_core import (
    intent_authority_is_current,
    lock_intent,
    transition_intent,
    transition_operation_digest,
)
from app.services.db_scope import run_with_db_scope


@dataclass(frozen=True)
class ApprovalClaim:
    intent_id: str = field(repr=False)
    state: str
    state_version: int
    expires_at: datetime
    approval_handle: str | None = field(repr=False)


@dataclass(frozen=True)
class TransitionResult:
    intent_id: str = field(repr=False)
    state: str
    state_version: int
    replay: bool = False


async def _checker_permission_current(
    conn: Any, user: Mapping[str, Any], permission: str
) -> bool:
    tenant_id, workspace_id = authority_scope(user)
    return await actor_has_current_permission(
        conn,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=actor_id(user),
        permission=permission,
    )


async def claim_intent_for_approval(
    user: Mapping[str, Any], intent_id: str
) -> ApprovalClaim:
    require_approve(user)
    tenant_id, workspace_id = authority_scope(user)
    checker_user_id = actor_id(user)
    pool = await auth.pool()

    async def _claim(
        conn: Any, scoped_tenant: str | None, scoped_workspace: str
    ) -> ApprovalClaim:
        if scoped_tenant != tenant_id or scoped_workspace != workspace_id:
            raise HTTPException(404, "action authority not found")
        intent = await lock_intent(
            conn,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            intent_id=intent_id,
        )
        require_distinct_actors(int(intent["maker_user_id"]), checker_user_id)
        if not await _checker_permission_current(conn, user, "control_room.approve"):
            raise HTTPException(403, "action authority is unavailable")
        existing_checker = intent.get("checker_user_id")
        if existing_checker is not None and int(existing_checker) != checker_user_id:
            raise HTTPException(404, "action authority not found")
        if str(intent["state"]) != "pending_approval":
            raise HTTPException(404, "action authority not found")
        if not await intent_authority_is_current(conn, intent):
            operation_digest = transition_operation_digest(
                intent,
                operation="claim_approval_stale",
                actor_user_id=checker_user_id,
            )
            stale = await transition_intent(
                conn,
                intent=intent,
                actor_user_id=checker_user_id,
                event_type="stale",
                operation_digest=operation_digest,
            )
            return ApprovalClaim(
                str(stale["id"]),
                "stale",
                int(stale["state_version"]),
                stale["expires_at"],
                None,
            )
        current = intent
        if existing_checker is None:
            operation_digest = transition_operation_digest(
                intent,
                operation="claim_approval",
                actor_user_id=checker_user_id,
            )
            current = await transition_intent(
                conn,
                intent=intent,
                actor_user_id=checker_user_id,
                event_type="approval_claimed",
                operation_digest=operation_digest,
                checker_user_id=checker_user_id,
            )
        token = await issue_stage_token(
            conn,
            user=user,
            intent_id=intent_id,
            stage="approval",
            intent_expires_at=current["expires_at"],
        )
        return ApprovalClaim(
            intent_id,
            str(current["state"]),
            int(current["state_version"]),
            current["expires_at"],
            token.handle,
        )

    return await run_with_db_scope(pool, dict(user), _claim)


async def _decide_intent(
    user: Mapping[str, Any],
    approval_handle: str,
    operation: Literal["approve", "reject"],
) -> TransitionResult:
    require_approve(user)
    tenant_id, workspace_id = authority_scope(user)
    checker_user_id = actor_id(user)
    pool = await auth.pool()

    async def _decide(
        conn: Any, scoped_tenant: str | None, scoped_workspace: str
    ) -> TransitionResult:
        if scoped_tenant != tenant_id or scoped_workspace != workspace_id:
            raise HTTPException(404, "action authority not found")
        intent_id = await peek_stage_token_intent(
            conn,
            user=user,
            handle=approval_handle,
            stage="approval",
        )
        intent = await lock_intent(
            conn,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            intent_id=intent_id,
        )
        token = await claim_stage_token(
            conn,
            user=user,
            handle=approval_handle,
            stage="approval",
            operation=operation,
        )
        if token.replay:
            return TransitionResult(
                token.intent_id,
                str(token.result_state),
                int(token.result_version or 0),
                True,
            )
        require_distinct_actors(int(intent["maker_user_id"]), checker_user_id)
        if int(intent.get("checker_user_id") or 0) != checker_user_id:
            raise HTTPException(404, "action authority not found")
        if not await _checker_permission_current(conn, user, "control_room.approve"):
            raise HTTPException(403, "action authority is unavailable")
        event_type = "approved" if operation == "approve" else "rejected"
        if not await intent_authority_is_current(conn, intent):
            event_type = "stale"
        updated = await transition_intent(
            conn,
            intent=intent,
            actor_user_id=checker_user_id,
            event_type=event_type,
            operation_digest=token.operation_digest,
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

    return await run_with_db_scope(pool, dict(user), _decide)


async def approve_intent(
    user: Mapping[str, Any], approval_handle: str
) -> TransitionResult:
    return await _decide_intent(user, approval_handle, "approve")


async def reject_intent(
    user: Mapping[str, Any], approval_handle: str
) -> TransitionResult:
    return await _decide_intent(user, approval_handle, "reject")


__all__ = (
    "ApprovalClaim",
    "TransitionResult",
    "approve_intent",
    "claim_intent_for_approval",
    "reject_intent",
)
