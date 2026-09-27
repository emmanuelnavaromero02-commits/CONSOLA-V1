from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any
from uuid import uuid4

import asyncpg
from fastapi import HTTPException

from app.services import auth, control_room_service
from app.services.control_room.business_action_attempt_policy import (
    binding_attempt_lock_key,
)
from app.services.control_room.business_action_authoritative_item import (
    AuthorityItemContract,
    contract_from_persisted_row,
)
from app.services.control_room.business_action_authority_policy import (
    EXECUTABLE_TEMPLATE_ID,
    actor_id,
    authority_scope,
    require_write,
)
from app.services.control_room.business_action_authority_repository import (
    fetch_authoritative_row_for_update,
)
from app.services.control_room.business_action_authorization_snapshot import (
    capture_authorization_snapshot,
)
from app.services.control_room.business_action_catalog import (
    require_enabled_action_template,
)
from app.services.control_room.business_action_dry_run_authority import (
    link_authority_dry_run,
    require_authority_dry_run,
)
from app.services.control_room.business_action_intent_store import (
    find_binding_intent,
    insert_intent,
    intent_matches_contract,
)
from app.services.control_room.business_action_ledger import append_intent_event
from app.services.control_room.business_action_registry import ACTION_TEMPLATES
from app.services.control_room.business_action_tokens import (
    server_binding_operation_digest,
)
from app.services.control_room.business_action_transition_core import (
    transition_intent,
    transition_operation_digest,
)
from app.services.control_room.business_execution_precondition import (
    dry_run_metadata,
)
from app.services.db_scope import run_with_db_scope


_LOGGER = logging.getLogger(__name__)
_PREPARE_FAILURE = "control_room_followup_intent_not_prepared"


async def _record_followup_dry_run(
    conn: Any, user: Mapping[str, Any], contract: AuthorityItemContract
) -> int | None:
    item = {**dict(contract.item), "id": contract.item_id}
    template = control_room_service._template_with_writeback(
        dict(ACTION_TEMPLATES[EXECUTABLE_TEMPLATE_ID])
    )
    payload = control_room_service._execution_payload(item, "dry_run", template)
    checks, warnings, ok = control_room_service._dry_run_checks(
        user=dict(user), item=item, template=template, payload=payload
    )
    if not ok:
        return None
    result = {
        "ok": True,
        "mode": "dry_run",
        "validated": True,
        "external_write": False,
        "warnings": warnings,
        "checks": checks,
        "message": "Dry-run validado. V1 no escribe en sistemas externos.",
    }
    run = await control_room_service._record_action_run(
        conn,
        user=dict(user),
        item=item,
        template=template,
        mode="dry_run",
        status="dry_run_completed",
        input_payload=payload,
        dry_run_result=result,
        idempotency_key=f"council-followup-dry-run:{uuid4().hex}",
        metadata={
            "checks": checks,
            "origin": "control_room_council",
            **dry_run_metadata(item, template_id=EXECUTABLE_TEMPLATE_ID),
        },
        critical=True,
    )
    run_id = run.get("id")
    return int(run_id) if run_id is not None else None


async def _prepare(
    conn: Any, user: Mapping[str, Any], item_id: str
) -> str | None:
    tenant_id, workspace_id = authority_scope(user)
    maker_user_id = actor_id(user)
    try:
        await require_enabled_action_template(conn, EXECUTABLE_TEMPLATE_ID)
    except HTTPException as exc:
        if exc.status_code == 404:
            return None
        raise
    authorization = await capture_authorization_snapshot(
        conn,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        actor_user_id=maker_user_id,
        permission="control_room.write",
    )
    if authorization is None:
        return None
    await conn.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
        binding_attempt_lock_key(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            maker_user_id=maker_user_id,
            item_id=item_id,
        ),
    )
    row = await fetch_authoritative_row_for_update(
        conn, tenant_id=tenant_id, workspace_id=workspace_id, item_id=item_id
    )
    contract = (
        contract_from_persisted_row(row, authorization=authorization)
        if row is not None
        else None
    )
    if contract is None:
        return None
    existing = await find_binding_intent(
        conn,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        maker_user_id=maker_user_id,
        item_id=item_id,
    )
    if existing is not None:
        if str(existing.get("state") or "") != "pending_approval":
            return None
        if intent_matches_contract(existing, contract):
            return str(existing["id"])
        await transition_intent(
            conn,
            intent=existing,
            actor_user_id=maker_user_id,
            event_type="stale",
            operation_digest=transition_operation_digest(
                existing, operation="council_superseded", actor_user_id=maker_user_id
            ),
        )
    dry_run_id = await _record_followup_dry_run(conn, user, contract)
    if dry_run_id is None:
        return None
    dry_run = await require_authority_dry_run(
        conn, user=user, contract=contract, action_run_id=dry_run_id
    )
    intent_id = str(uuid4())
    intent = await insert_intent(
        conn,
        contract=contract,
        dry_run=dry_run,
        intent_id=intent_id,
        correlation_id=str(uuid4()),
    )
    if intent is None:
        return None
    await link_authority_dry_run(
        conn, dry_run=dry_run, contract=contract, intent_id=intent_id
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
        operation_digest=server_binding_operation_digest(
            workspace_id=workspace_id,
            dry_run_action_run_id=dry_run.action_run_id,
            dry_run_evidence_digest=dry_run.evidence_digest,
            maker_user_id=maker_user_id,
            binding_digest=contract.binding_digest,
            contract_digest=contract.contract_digest,
        ),
    )
    return intent_id


async def prepare_followup_intent(
    user: Mapping[str, Any], item_id: str
) -> str | None:
    """Maker side of the council: a 24 h pending intent for the item's follow-up."""
    require_write(user)
    pool = await auth.pool()

    async def _run(conn: Any, _tenant_id: str | None, _workspace_id: str) -> str | None:
        return await _prepare(conn, user, item_id)

    try:
        return await run_with_db_scope(pool, dict(user), _run)
    except asyncpg.ExclusionViolationError:
        return None


async def prepare_followup_intent_safely(
    user: Mapping[str, Any], item_id: str
) -> str | None:
    try:
        return await prepare_followup_intent(user, item_id)
    except Exception:
        _LOGGER.warning(
            _PREPARE_FAILURE,
            extra={"event": _PREPARE_FAILURE, "component": "control_room_council"},
        )
        return None


__all__ = ("prepare_followup_intent", "prepare_followup_intent_safely")
