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
from app.services.control_room.business_action_attempt_policy import (
    binding_attempt_lock_key,
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
    revoke_action_binding_token,
)
from app.services.control_room.business_action_catalog import (
    load_enabled_action_template_ids,
    require_enabled_action_template,
)
from app.services.control_room.business_action_ledger import append_intent_event
from app.services.control_room.business_action_dry_run_authority import (
    link_authority_dry_run,
    require_authority_dry_run,
)
from app.services.control_room.business_action_intent_store import (
    find_binding_intent,
    insert_intent,
    intent_matches_contract,
)
from app.services.control_room.business_action_registry import ACTION_TEMPLATES
from app.services.control_room.business_action_revalidation import (
    actor_has_current_permission,
)
from app.services.control_room.business_action_tokens import (
    IssuedStageHandle,
    issue_stage_token,
    server_binding_operation_digest,
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
        peek = await resolve_action_binding_token(
            conn,
            user=user,
            action_handle=action_handle,
        )
        item_id = str(peek.get("item_id") or "")
        await conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
            binding_attempt_lock_key(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                maker_user_id=maker_user_id,
                item_id=item_id,
            ),
        )
        token = await resolve_action_binding_token(
            conn,
            user=user,
            action_handle=action_handle,
            for_update=True,
        )
        if str(token.get("id") or "") != str(peek.get("id") or ""):
            raise HTTPException(404, "action authority not found")
        existing = await find_binding_intent(
            conn,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            maker_user_id=maker_user_id,
            item_id=item_id,
        )
        reuse_intent_id = str(token.get("intent_id") or "")
        if reuse_intent_id and (
            existing is None or str(existing.get("id") or "") != reuse_intent_id
        ):
            raise HTTPException(404, "action authority not found")
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
        if existing is not None:
            if (
                not intent_matches_contract(existing, contract)
                or str(existing.get("state") or "") != "pending_approval"
            ):
                raise HTTPException(409, "action intent already exists")
            token_dry_run_id = token.get("binding_dry_run_action_run_id")
            if token_dry_run_id is not None and int(
                existing["dry_run_action_run_id"]
            ) != int(token_dry_run_id):
                raise HTTPException(404, "action authority not found")
            await require_authority_dry_run(
                conn,
                user=user,
                contract=contract,
                action_run_id=int(existing["dry_run_action_run_id"]),
                expected_evidence_digest=str(existing["dry_run_evidence_digest"]),
                expected_intent_id=str(existing["id"]),
            )
            await revoke_action_binding_token(
                conn, token_id=str(token["id"]), user=user
            )
            workflow = await issue_stage_token(
                conn,
                user=user,
                intent_id=str(existing["id"]),
                stage="workflow",
                intent_expires_at=existing["expires_at"],
            )
            return PromotedIntent(
                str(existing["id"]),
                str(existing["state"]),
                int(existing["state_version"]),
                existing["expires_at"],
                workflow.handle,
            )
        token_dry_run_id = token.get("binding_dry_run_action_run_id")
        token_dry_run_evidence = str(token.get("binding_dry_run_evidence_digest") or "")
        dry_run = await require_authority_dry_run(
            conn,
            user=user,
            contract=contract,
            action_run_id=(
                int(token_dry_run_id) if token_dry_run_id is not None else None
            ),
            expected_evidence_digest=token_dry_run_evidence or None,
        )
        intent_id = str(uuid4())
        correlation_id = str(uuid4())
        operation_digest = server_binding_operation_digest(
            workspace_id=workspace_id,
            dry_run_action_run_id=dry_run.action_run_id,
            dry_run_evidence_digest=dry_run.evidence_digest,
            maker_user_id=maker_user_id,
            binding_digest=contract.binding_digest,
            contract_digest=contract.contract_digest,
        )
        intent = await insert_intent(
            conn,
            contract=contract,
            dry_run=dry_run,
            intent_id=intent_id,
            correlation_id=correlation_id,
        )
        if intent is None:
            raise HTTPException(409, "action intent already exists")
        await link_authority_dry_run(
            conn,
            dry_run=dry_run,
            contract=contract,
            intent_id=intent_id,
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
