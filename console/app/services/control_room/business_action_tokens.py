from __future__ import annotations

import hashlib
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import HTTPException

from app.services.control_room.business_action_digest import action_contract_digest
from app.services.control_room.business_action_authority_policy import (
    EXECUTION_HANDLE_TTL_SECONDS,
    actor_id,
    authority_scope,
)


TokenStage = Literal["workflow", "approval", "execution"]
_HANDLE = re.compile(r"^[0-9a-f]{64}$")
_STAGE_TTL = {
    "workflow": 24 * 60 * 60,
    "approval": 24 * 60 * 60,
    "execution": EXECUTION_HANDLE_TTL_SECONDS,
}


@dataclass(frozen=True)
class IssuedStageHandle:
    intent_id: str = field(repr=False)
    stage: TokenStage
    expires_at: datetime
    handle: str = field(repr=False)


@dataclass(frozen=True)
class ClaimedStageToken:
    token_id: str = field(repr=False)
    intent_id: str = field(repr=False)
    stage: TokenStage
    operation_digest: str
    replay: bool
    result_state: str | None
    result_version: int | None


def new_handle() -> str:
    return secrets.token_bytes(32).hex()


def handle_digest(handle: str) -> bytes:
    if not isinstance(handle, str) or _HANDLE.fullmatch(handle) is None:
        raise HTTPException(404, "action authority not found")
    return hashlib.sha256(bytes.fromhex(handle)).digest()


def server_operation_digest(
    *,
    workspace_id: str,
    intent_id: str,
    operation: str,
    contract_digest: str,
    actor_user_id: int,
) -> str:
    return action_contract_digest(
        {
            "workspace_id": str(workspace_id),
            "intent_id": str(intent_id),
            "operation": str(operation),
            "contract_digest": str(contract_digest),
            "actor_user_id": int(actor_user_id),
        }
    )


def server_binding_operation_digest(
    *,
    workspace_id: str,
    maker_user_id: int,
    binding_digest: str,
    contract_digest: str,
) -> str:
    return action_contract_digest(
        {
            "workspace_id": str(workspace_id),
            "maker_user_id": int(maker_user_id),
            "binding_digest": str(binding_digest),
            "operation": "promote",
            "contract_digest": str(contract_digest),
        }
    )


async def issue_stage_token(
    conn: Any,
    *,
    user: Mapping[str, Any],
    intent_id: str,
    stage: TokenStage,
    intent_expires_at: datetime,
    now: datetime | None = None,
) -> IssuedStageHandle:
    if stage not in _STAGE_TTL:
        raise ValueError("action authority token stage is invalid")
    tenant_id, workspace_id = authority_scope(user)
    subject_user_id = actor_id(user)
    issued_at = (now or datetime.now(UTC)).astimezone(UTC)
    expires_at = min(
        intent_expires_at.astimezone(UTC),
        issued_at + timedelta(seconds=_STAGE_TTL[stage]),
    )
    if expires_at <= issued_at:
        raise HTTPException(404, "action authority not found")
    handle = new_handle()
    row = await conn.fetchrow(
        """
        INSERT INTO control_room_action_tokens (
            tenant_id, workspace_id, intent_id, stage, subject_user_id,
            token_digest, issued_at, expires_at
        ) VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5, $6, $7, $8)
        ON CONFLICT (
            tenant_id, workspace_id, intent_id, stage, subject_user_id
        ) WHERE stage <> 'action_binding' AND status = 'active'
        DO UPDATE SET
            token_digest = EXCLUDED.token_digest,
            issued_at = EXCLUDED.issued_at,
            expires_at = EXCLUDED.expires_at,
            operation_digest = NULL,
            result_state = NULL,
            result_version = NULL,
            consumed_at = NULL,
            consumed_by = NULL
        RETURNING id
        """,
        tenant_id,
        workspace_id,
        intent_id,
        stage,
        subject_user_id,
        handle_digest(handle),
        issued_at,
        expires_at,
    )
    if not row:
        raise RuntimeError("control room action stage token insert failed")
    return IssuedStageHandle(intent_id, stage, expires_at, handle)


async def claim_stage_token(
    conn: Any,
    *,
    user: Mapping[str, Any],
    handle: str,
    stage: TokenStage,
    operation: str,
) -> ClaimedStageToken:
    tenant_id, workspace_id = authority_scope(user)
    subject_user_id = actor_id(user)
    digest = handle_digest(handle)
    row = await conn.fetchrow(
        """
        SELECT token.id::text AS token_id, token.intent_id::text,
               token.stage, token.status, token.subject_user_id,
               token.operation_digest, token.result_state,
               token.result_version, token.expires_at, intent.contract_digest
          FROM control_room_action_tokens AS token
          JOIN control_room_action_intents AS intent
            ON intent.tenant_id = token.tenant_id
           AND intent.workspace_id = token.workspace_id
           AND intent.id = token.intent_id
         WHERE token.tenant_id = $1::uuid AND token.workspace_id = $2::uuid
           AND token.token_digest = $3 AND token.stage = $4
           AND token.subject_user_id = $5 AND token.expires_at > NOW()
         FOR UPDATE OF token
        """,
        tenant_id,
        workspace_id,
        digest,
        stage,
        subject_user_id,
    )
    if not row or not row.get("intent_id"):
        raise HTTPException(404, "action authority not found")
    intent_id = str(row["intent_id"])
    operation_digest = server_operation_digest(
        workspace_id=workspace_id,
        intent_id=intent_id,
        operation=operation,
        contract_digest=str(row.get("contract_digest") or ""),
        actor_user_id=subject_user_id,
    )
    if row.get("status") == "consumed":
        if str(row.get("operation_digest") or "") != operation_digest:
            raise HTTPException(404, "action authority not found")
        return ClaimedStageToken(
            str(row["token_id"]),
            intent_id,
            stage,
            operation_digest,
            True,
            str(row.get("result_state") or "") or None,
            int(row["result_version"])
            if row.get("result_version") is not None
            else None,
        )
    if row.get("status") != "active":
        raise HTTPException(404, "action authority not found")
    return ClaimedStageToken(
        str(row["token_id"]), intent_id, stage, operation_digest, False, None, None
    )


async def peek_stage_token_intent(
    conn: Any,
    *,
    user: Mapping[str, Any],
    handle: str,
    stage: TokenStage,
) -> str:
    tenant_id, workspace_id = authority_scope(user)
    row = await conn.fetchrow(
        """
        SELECT intent_id::text AS intent_id
          FROM control_room_action_tokens
         WHERE tenant_id=$1::uuid AND workspace_id=$2::uuid
           AND token_digest=$3 AND stage=$4 AND subject_user_id=$5
           AND status IN ('active', 'consumed') AND expires_at > NOW()
        """,
        tenant_id,
        workspace_id,
        handle_digest(handle),
        stage,
        actor_id(user),
    )
    if not row or not row.get("intent_id"):
        raise HTTPException(404, "action authority not found")
    return str(row["intent_id"])


async def consume_stage_token(
    conn: Any,
    *,
    token: ClaimedStageToken,
    user: Mapping[str, Any],
    result_state: str,
    result_version: int,
) -> None:
    if token.replay:
        return
    tenant_id, workspace_id = authority_scope(user)
    updated = await conn.fetchrow(
        """
        UPDATE control_room_action_tokens
           SET status = 'consumed', operation_digest = $1,
               result_state = $2, result_version = $3,
               consumed_at = NOW(), consumed_by = $4
         WHERE tenant_id = $5::uuid AND workspace_id = $6::uuid
           AND id = $7::uuid AND intent_id = $8::uuid AND stage = $9
           AND subject_user_id = $4 AND status = 'active'
         RETURNING id
        """,
        token.operation_digest,
        result_state,
        int(result_version),
        actor_id(user),
        tenant_id,
        workspace_id,
        token.token_id,
        token.intent_id,
        token.stage,
    )
    if not updated:
        raise HTTPException(404, "action authority not found")


__all__ = (
    "ClaimedStageToken",
    "IssuedStageHandle",
    "claim_stage_token",
    "consume_stage_token",
    "handle_digest",
    "issue_stage_token",
    "new_handle",
    "peek_stage_token_intent",
    "server_binding_operation_digest",
    "server_operation_digest",
)
