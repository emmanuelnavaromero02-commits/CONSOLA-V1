from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import uuid4

from fastapi import HTTPException

from app.services import auth
from app.services.control_room.business_action_authoritative_item import (
    AuthorityItemContract,
    match_authoritative_item,
)
from app.services.control_room.business_action_authority_policy import (
    EXECUTABLE_TEMPLATE_ID,
    actor_id,
    authority_scope,
    require_write,
)
from app.services.control_room.business_action_authority_repository import (
    consume_action_binding_token,
    fetch_authoritative_row_for_update,
    resolve_action_binding_token,
)
from app.services.control_room.business_action_catalog import (
    load_enabled_action_template_ids,
    require_enabled_action_template,
)
from app.services.control_room.business_action_ledger import append_intent_event
from app.services.control_room.business_action_registry import ACTION_TEMPLATES
from app.services.control_room.business_action_revalidation import (
    actor_has_current_permission,
)
from app.services.control_room.business_action_tokens import (
    IssuedStageHandle,
    issue_stage_token,
    server_operation_digest,
)
from app.services.control_room.surface_snapshot import collect_surface_snapshot
from app.services.db_scope import run_with_db_scope


@dataclass(frozen=True)
class PromotedIntent:
    intent_id: str = field(repr=False)
    state: str
    state_version: int
    expires_at: datetime
    workflow_handle: str = field(repr=False)


def _token_matches(token: Mapping[str, Any], contract: AuthorityItemContract) -> bool:
    expected = {
        "item_id": contract.item_id,
        "template_id": EXECUTABLE_TEMPLATE_ID,
        "binding_digest": contract.binding_digest,
        "evidence_digest": contract.evidence_digest,
        "observation_fingerprint": contract.observation_fingerprint,
        "contract_digest": contract.contract_digest,
        "target_digest": contract.target_digest,
        "decision_digest": contract.decision_digest,
    }
    return all(str(token.get(key) or "") == value for key, value in expected.items())


async def _insert_intent(
    conn: Any,
    *,
    contract: AuthorityItemContract,
    intent_id: str,
    correlation_id: str,
) -> dict[str, Any]:
    row = await conn.fetchrow(
        """
        INSERT INTO control_room_action_intents (
            id, tenant_id, workspace_id, item_id, maker_user_id, template_id,
            binding_digest, evidence_digest, observation_fingerprint,
            contract_digest, target_digest, dry_run_digest, decision_digest,
            state, state_version, result_code, correlation_id, expires_at
        ) VALUES (
            $1::uuid, $2::uuid, $3::uuid, $4, $5, 'create_followup_task',
            $6, $7, $8, $9, $10, $11, $12,
            'pending_approval', 1, 'created', $13::uuid,
            NOW() + INTERVAL '24 hours'
        )
        RETURNING *, tenant_id::text AS tenant_id,
                     workspace_id::text AS workspace_id, id::text AS id,
                     correlation_id::text AS correlation_id
        """,
        intent_id,
        contract.tenant_id,
        contract.workspace_id,
        contract.item_id,
        contract.maker_user_id,
        contract.binding_digest,
        contract.evidence_digest,
        contract.observation_fingerprint,
        contract.contract_digest,
        contract.target_digest,
        contract.dry_run_digest(),
        contract.decision_digest,
        correlation_id,
    )
    if not row:
        raise RuntimeError("control room action intent insert failed")
    return dict(row)


async def promote_action_handle(
    user: Mapping[str, Any], action_handle: str
) -> PromotedIntent:
    require_write(user)
    tenant_id, workspace_id = authority_scope(user)
    maker_user_id = actor_id(user)
    snapshot = await collect_surface_snapshot(user)
    enabled = await load_enabled_action_template_ids(user)
    if EXECUTABLE_TEMPLATE_ID not in enabled:
        raise HTTPException(404, "action authority not found")
    pool = await auth.pool()

    async def _promote(
        conn: Any, scoped_tenant: str | None, scoped_workspace: str
    ) -> PromotedIntent:
        if scoped_tenant != tenant_id or scoped_workspace != workspace_id:
            raise HTTPException(404, "action authority not found")
        if not await actor_has_current_permission(
            conn,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=maker_user_id,
            permission="control_room.write",
        ):
            raise HTTPException(403, "action authority is unavailable")
        token = await resolve_action_binding_token(
            conn,
            user=user,
            action_handle=action_handle,
            for_update=True,
        )
        item_id = str(token.get("item_id") or "")
        live = next(
            (
                item
                for item in snapshot.items
                if str(item.get("id") or item.get("item_id") or "") == item_id
            ),
            None,
        )
        await require_enabled_action_template(conn, EXECUTABLE_TEMPLATE_ID)
        row = await fetch_authoritative_row_for_update(
            conn,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            item_id=item_id,
        )
        contract = (
            match_authoritative_item(
                live,
                row,
                user,
                ACTION_TEMPLATES[EXECUTABLE_TEMPLATE_ID],
            )
            if live is not None and row is not None
            else None
        )
        if contract is None or not _token_matches(token, contract):
            raise HTTPException(404, "action authority not found")
        intent_id = str(uuid4())
        correlation_id = str(uuid4())
        operation_digest = server_operation_digest(
            workspace_id=workspace_id,
            intent_id=intent_id,
            operation="promote",
            contract_digest=contract.contract_digest,
            actor_user_id=maker_user_id,
        )
        intent = await _insert_intent(
            conn,
            contract=contract,
            intent_id=intent_id,
            correlation_id=correlation_id,
        )
        await append_intent_event(
            conn,
            intent=intent,
            actor_user_id=maker_user_id,
            event_type="intent_created",
            from_state="none",
            to_state="pending_approval",
            intent_version=1,
            result_code="created",
            operation_digest=operation_digest,
        )
        await consume_action_binding_token(
            conn,
            token_id=str(token["id"]),
            user=user,
            operation_digest=operation_digest,
            result_state="pending_approval",
            result_version=1,
        )
        workflow: IssuedStageHandle = await issue_stage_token(
            conn,
            user=user,
            intent_id=intent_id,
            stage="workflow",
            intent_expires_at=intent["expires_at"],
        )
        return PromotedIntent(
            intent_id,
            "pending_approval",
            1,
            intent["expires_at"],
            workflow.handle,
        )

    return await run_with_db_scope(pool, dict(user), _promote)


__all__ = ("PromotedIntent", "promote_action_handle")
