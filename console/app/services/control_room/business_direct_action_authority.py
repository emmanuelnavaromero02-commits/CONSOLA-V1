from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException

from app.services import auth
from app.services.control_room.business_action_authority_policy import (
    DIRECT_ACTION_TEMPLATE_IDS,
    actor_id,
    authority_scope,
)
from app.services.control_room.business_action_authority_repository import (
    consume_action_binding_token,
    fetch_direct_row_for_update,
    lock_action_binding_token_for_mutation,
)
from app.services.control_room.business_action_authorization_binding import (
    token_authorization_matches,
)
from app.services.control_room.business_action_authorization_snapshot import (
    AuthorizationSnapshot,
    capture_authorization_snapshot,
)
from app.services.control_room.business_action_digest import action_contract_digest
from app.services.control_room.business_action_direct_contract import (
    DirectActionContract,
    direct_contract_from_persisted_row,
    token_matches_direct_contract,
)
from app.services.control_room.business_action_registry import ACTION_TEMPLATES
from app.services.control_room.business_action_tokens import handle_digest
from app.services.db_scope import run_with_db_scope


OPERATION_VERSION = "control-room-direct-action-operation/v1"
REPLAY_SQL = """
SELECT id::text AS id, item_id, template_id, operation_digest, result_state
  FROM control_room_action_tokens
 WHERE tenant_id = $1::uuid AND workspace_id = $2::uuid
   AND stage = 'action_binding' AND subject_user_id = $3
   AND token_digest = $4 AND status = 'consumed'
"""


@dataclass(frozen=True, repr=False)
class LockedDirectAction:
    token: Mapping[str, Any] = field(repr=False)
    contract: DirectActionContract = field(repr=False)
    row: Mapping[str, Any] = field(repr=False)
    authorization: AuthorizationSnapshot = field(repr=False)


@dataclass(frozen=True, repr=False)
class DirectActionReplay:
    item_id: str = field(repr=False)
    template_id: str
    result_state: str


def changed_error() -> HTTPException:
    return HTTPException(
        409,
        {
            "code": "item_business_state_changed",
            "message": "control room item changed; reload before mutating",
        },
    )


def not_found_error() -> HTTPException:
    return HTTPException(404, "action binding not found")


def operation_digest(
    *, token_id: str, template_id: str, idempotency_key: str | None
) -> str:
    return action_contract_digest(
        {
            "version": OPERATION_VERSION,
            "token_id": token_id,
            "template_id": template_id,
            "idempotency_key": idempotency_key,
        }
    )


async def lock_direct_action(
    conn: Any,
    *,
    user: Mapping[str, Any],
    action_handle: str,
    item_id: str,
    template_id: str,
) -> LockedDirectAction:
    if template_id not in DIRECT_ACTION_TEMPLATE_IDS:
        raise not_found_error()
    tenant_id, workspace_id = authority_scope(user)
    token = await lock_action_binding_token_for_mutation(
        conn, user=user, action_handle=action_handle, item_id=item_id
    )
    if str(token.get("template_id") or "") != template_id:
        raise not_found_error()
    authorization = await capture_authorization_snapshot(
        conn,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        actor_user_id=actor_id(user),
        permission="control_room.write",
    )
    if authorization is None or not token_authorization_matches(token, authorization):
        raise not_found_error()
    row = await fetch_direct_row_for_update(
        conn, tenant_id=tenant_id, workspace_id=workspace_id, item_id=item_id
    )
    if row is None:
        raise changed_error()
    contract = direct_contract_from_persisted_row(
        row,
        authorization=authorization,
        template=ACTION_TEMPLATES[template_id],
        user=user,
    )
    if contract is None or not token_matches_direct_contract(token, contract):
        raise changed_error()
    return LockedDirectAction(token, contract, row, authorization)


async def consume_direct_action(
    conn: Any,
    locked: LockedDirectAction,
    *,
    user: Mapping[str, Any],
    idempotency_key: str | None,
    result_state: str,
) -> None:
    await consume_action_binding_token(
        conn,
        token_id=str(locked.token["id"]),
        user=user,
        operation_digest=operation_digest(
            token_id=str(locked.token["id"]),
            template_id=locked.contract.template_id,
            idempotency_key=idempotency_key,
        ),
        result_state=result_state,
        result_version=1,
    )


async def find_direct_action_replay(
    user: Mapping[str, Any],
    *,
    action_handle: str,
    template_id: str,
    idempotency_key: str | None,
) -> DirectActionReplay | None:
    if idempotency_key is None or template_id not in DIRECT_ACTION_TEMPLATE_IDS:
        return None
    tenant_id, workspace_id = authority_scope(user)
    maker_user_id = actor_id(user)
    pool = await auth.pool()

    async def _read(conn: Any, scoped_tenant: str | None, scoped_workspace: str):
        if scoped_tenant != tenant_id or scoped_workspace != workspace_id:
            return None
        return await conn.fetchrow(
            REPLAY_SQL,
            tenant_id,
            workspace_id,
            maker_user_id,
            handle_digest(action_handle),
        )

    row = await run_with_db_scope(pool, dict(user), _read)
    if not row or str(row["template_id"] or "") != template_id:
        return None
    expected = operation_digest(
        token_id=str(row["id"]),
        template_id=template_id,
        idempotency_key=idempotency_key,
    )
    if str(row["operation_digest"] or "") != expected:
        return None
    return DirectActionReplay(
        item_id=str(row["item_id"]),
        template_id=template_id,
        result_state=str(row["result_state"] or ""),
    )


__all__ = (
    "DirectActionReplay",
    "LockedDirectAction",
    "REPLAY_SQL",
    "changed_error",
    "consume_direct_action",
    "find_direct_action_replay",
    "lock_direct_action",
    "not_found_error",
    "operation_digest",
)
