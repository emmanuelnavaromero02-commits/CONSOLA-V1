from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException

from app.schemas.control_room_direct_actions import (
    PROPOSAL_CREATED_MESSAGE,
    PROPOSAL_EXISTS_MESSAGE,
    DecisionProposalResponse,
)
from app.services import audit_service, auth, control_room_service
from app.services.control_room.business_access import owner_scope_id
from app.services.control_room.business_action_authority_policy import (
    DECISION_PROPOSAL_TEMPLATE_ID,
    authority_scope,
)
from app.services.control_room.business_action_handle import (
    resolve_business_action_handle,
)
from app.services.control_room.business_direct_action_authority import (
    changed_error,
    consume_direct_action,
    find_direct_action_replay,
    lock_direct_action,
)
from app.services.control_room.business_followup_intent import (
    prepare_followup_intent_safely,
)
from app.services.db_scope import run_with_db_scope


PROPOSAL_CREATED = "proposal_created"
LINKED_DECISION_SQL = """
SELECT decision_id
  FROM control_room_items
 WHERE tenant_id = $1::uuid
   AND workspace_id = $2::uuid
   AND item_id = $3
   AND ($4::bigint IS NULL OR owner_user_id = $4::bigint)
 LIMIT 1
"""


def council_href(decision_id: int) -> str:
    if isinstance(decision_id, bool) or int(decision_id) <= 0:
        raise ValueError("decision id is invalid")
    return f"/decisions?tab=consejo&propuesta={int(decision_id)}"


def _response(
    action_handle: str, decision_id: int, *, created: bool
) -> DecisionProposalResponse:
    return DecisionProposalResponse(
        action_handle=action_handle,
        status=PROPOSAL_CREATED if created else "proposal_exists",
        decision_id=int(decision_id),
        href=council_href(decision_id),
        message=PROPOSAL_CREATED_MESSAGE if created else PROPOSAL_EXISTS_MESSAGE,
    )


async def _linked_decision_id(user: Mapping[str, Any], item_id: str) -> int:
    tenant_id, workspace_id = authority_scope(user)
    owner_id = owner_scope_id(user)
    pool = await auth.pool()

    async def _read(conn: Any, _tenant_id: str | None, _workspace_id: str) -> Any:
        return await conn.fetchrow(
            LINKED_DECISION_SQL, tenant_id, workspace_id, item_id, owner_id
        )

    row = await run_with_db_scope(pool, dict(user), _read)
    decision_id = row["decision_id"] if row else None
    if decision_id is None or isinstance(decision_id, bool) or int(decision_id) <= 0:
        raise changed_error()
    return int(decision_id)


async def create_decision_proposal(
    user: Mapping[str, Any],
    *,
    action_handle: str,
    idempotency_key: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> DecisionProposalResponse:
    replay = await find_direct_action_replay(
        user,
        action_handle=action_handle,
        template_id=DECISION_PROPOSAL_TEMPLATE_ID,
        idempotency_key=idempotency_key,
    )
    if replay is not None:
        if replay.result_state != PROPOSAL_CREATED:
            raise changed_error()
        return _response(
            action_handle,
            await _linked_decision_id(user, replay.item_id),
            created=False,
        )
    resolved = await resolve_business_action_handle(
        user,
        action_handle,
        allowed_template_ids=frozenset({DECISION_PROPOSAL_TEMPLATE_ID}),
    )
    item = await control_room_service._item_for_mutation(resolved.item_id, dict(user))
    pool = await auth.pool()

    async def _write(conn: Any, _tenant_id: str | None, workspace_id: str) -> int:
        locked = await lock_direct_action(
            conn,
            user=user,
            action_handle=action_handle,
            item_id=resolved.item_id,
            template_id=DECISION_PROPOSAL_TEMPLATE_ID,
        )
        if str(item.get("id") or "") != locked.contract.item_id:
            raise changed_error()
        decision = await control_room_service._create_and_link_business_decision(
            conn,
            user=dict(user),
            item=item,
            workspace_id=workspace_id,
            ensure_item_row=control_room_service._ensure_item_row,
            record_item_event=control_room_service._record_item_event,
        )
        decision_id = decision["id"] if decision else None
        if decision_id is None or int(decision_id) <= 0:
            raise HTTPException(409, "control room decision link is invalid")
        await audit_service.record_event(
            connection=conn,
            user_id=user.get("id"),
            email=user.get("email"),
            action="control_room.decision.create",
            resource_type="control_room_item",
            resource_id=locked.contract.item_id,
            ip=ip,
            user_agent=user_agent,
            status="success",
            metadata={
                "decision_id": int(decision_id),
                "template_id": DECISION_PROPOSAL_TEMPLATE_ID,
            },
            critical=True,
        )
        await consume_direct_action(
            conn,
            locked,
            user=user,
            idempotency_key=idempotency_key,
            result_state=PROPOSAL_CREATED,
        )
        return int(decision_id)

    decision_id = await run_with_db_scope(pool, dict(user), _write)
    await prepare_followup_intent_safely(user, resolved.item_id)
    return _response(action_handle, decision_id, created=True)


__all__ = (
    "LINKED_DECISION_SQL",
    "PROPOSAL_CREATED",
    "council_href",
    "create_decision_proposal",
)
