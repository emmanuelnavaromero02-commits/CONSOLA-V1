from __future__ import annotations

import json
import logging
from collections.abc import Collection, Iterable, Mapping, Sequence
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import HTTPException

from app.schemas.control_room_experience_actions import ExperienceAction
from app.services import auth
from app.services.control_room.business_action_authoritative_item import (
    match_authoritative_item,
)
from app.services.control_room.business_action_authority_policy import (
    APPROVE_EXCEPTION_TEMPLATE_ID,
    BINDING_TEMPLATE_ORDER,
    DECISION_PROPOSAL_TEMPLATE_ID,
    EXECUTABLE_TEMPLATE_ID,
    OPEN_IN_STUDIO_TEMPLATE_ID,
    REOPEN_EXCEPTION_TEMPLATE_ID,
    authority_scope,
    require_write,
)
from app.services.control_room.business_action_authority_repository import (
    fetch_authoritative_rows,
    fetch_direct_rows,
    insert_action_binding_token,
)
from app.services.control_room.business_action_catalog import (
    ENABLED_ACTION_TEMPLATE_IDS_SQL,
    matches_runtime_registry,
    require_enabled_action_template,
)
from app.services.control_room.business_action_direct_contract import (
    DirectActionContract,
    DirectMatch,
    classify_direct_action,
    match_reopen_item,
)
from app.services.control_room.business_action_public_projection import (
    PUBLIC_ACTION_KINDS,
    public_action,
)
from app.services.control_room.business_action_registry import ACTION_TEMPLATES
from app.services.control_room.business_action_authorization_snapshot import (
    AuthorizationSnapshot,
    capture_authorization_snapshot,
)
from app.services.control_room.business_direct_action_slots import (
    issue_direct_slots,
)
from app.services.control_room.surface_snapshot import SurfaceSnapshot
from app.services.db_scope import run_with_db_scope
from app.services.security_context import sign_server_payload


_LOGGER = logging.getLogger(__name__)
_OPERATIONAL_FAILURE = "control_room_action_binding_operational_failure"
_PLACEHOLDER_PURPOSE = "control-room-action-placeholder/v1"
NEEDS_REFRESH_REASON = "Actualiza los datos antes de continuar."
LIVE_DIRECT_ORDER = (
    APPROVE_EXCEPTION_TEMPLATE_ID,
    DECISION_PROPOSAL_TEMPLATE_ID,
    OPEN_IN_STUDIO_TEMPLATE_ID,
)


def _record_operational_failure() -> None:
    _LOGGER.error(
        _OPERATIONAL_FAILURE,
        extra={
            "event": _OPERATIONAL_FAILURE,
            "component": "control_room_action_authority",
            "outcome": "actions_omitted",
        },
    )


def placeholder_handle(
    *, tenant_id: str, workspace_id: str, user_id: int, item_id: str, template_id: str
) -> str:
    payload = json.dumps(
        {
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "user_id": user_id,
            "item_id": item_id,
            "template_id": template_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sign_server_payload(payload, purpose=_PLACEHOLDER_PURPOSE)


def refresh_required_action(handle: str, template_id: str) -> ExperienceAction:
    template = ACTION_TEMPLATES[template_id]
    return ExperienceAction(
        action_handle=handle,
        kind=PUBLIC_ACTION_KINDS[template_id],
        label=str(template["label"]),
        enabled=False,
        requires_approval=bool(template["requires_approval"]),
        disabled_reason=NEEDS_REFRESH_REASON,
    )


@asynccontextmanager
async def _savepoint(conn: Any) -> AsyncIterator[None]:
    transaction = getattr(conn, "transaction", None)
    if not callable(transaction):
        yield
        return
    async with transaction():
        yield


async def _enabled_now(conn: Any, template_ids: Sequence[str]) -> frozenset[str]:
    rows = await conn.fetch(
        ENABLED_ACTION_TEMPLATE_IDS_SQL, list(template_ids), len(template_ids)
    )
    return frozenset(
        str(row["template_id"]) for row in rows if matches_runtime_registry(row)
    )


async def _capture(
    conn: Any, user: Mapping[str, Any], tenant_id: str, workspace_id: str
) -> AuthorizationSnapshot | None:
    return await capture_authorization_snapshot(
        conn,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        actor_user_id=int(user["id"]),
        permission="control_room.write",
    )


async def _slot_handles(
    conn: Any, contracts: Sequence[DirectActionContract]
) -> dict[tuple[str, str], str]:
    try:
        async with _savepoint(conn):
            return await issue_direct_slots(conn, contracts)
    except Exception:
        _record_operational_failure()
        return {}


async def _issue_direct(
    user: Mapping[str, Any],
    live_by_id: Mapping[str, Mapping[str, Any]],
    template_ids: Sequence[str],
    tenant_id: str,
    workspace_id: str,
) -> dict[str, dict[str, ExperienceAction]]:
    async def _issue(
        conn: Any, scoped_tenant_id: str | None, scoped_workspace_id: str
    ) -> dict[str, dict[str, ExperienceAction]]:
        if scoped_tenant_id != tenant_id or scoped_workspace_id != workspace_id:
            return {}
        authorization = await _capture(conn, user, tenant_id, workspace_id)
        if authorization is None:
            return {}
        enabled = await _enabled_now(conn, template_ids)
        templates = [
            template_id for template_id in template_ids if template_id in enabled
        ]
        if not templates:
            return {}
        rows = await fetch_direct_rows(
            conn,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            item_ids=tuple(live_by_id),
        )
        matched: dict[str, list[DirectActionContract]] = {
            template_id: [] for template_id in templates
        }
        refresh: list[tuple[str, str]] = []
        for item_id, live in live_by_id.items():
            for template_id in templates:
                status, contract = classify_direct_action(
                    live,
                    rows.get(item_id),
                    authorization,
                    ACTION_TEMPLATES[template_id],
                    user=user,
                )
                if status is DirectMatch.MATCH and contract is not None:
                    matched[template_id].append(contract)
                elif status is DirectMatch.NEEDS_REFRESH:
                    refresh.append((item_id, template_id))
        issued: dict[str, dict[str, ExperienceAction]] = {}
        for template_id in templates:
            contracts = matched[template_id]
            if not contracts:
                continue
            handles = await _slot_handles(conn, contracts)
            for contract in contracts:
                handle = handles.get((contract.item_id, template_id))
                if handle is not None:
                    issued.setdefault(contract.item_id, {})[template_id] = (
                        public_action(handle, template_id=template_id)
                    )
        for item_id, template_id in refresh:
            issued.setdefault(item_id, {})[template_id] = refresh_required_action(
                placeholder_handle(
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    user_id=authorization.actor_user_id,
                    item_id=item_id,
                    template_id=template_id,
                ),
                template_id,
            )
        return issued

    pool = await auth.pool()
    return await run_with_db_scope(pool, dict(user), _issue)


async def _issue_followups(
    user: Mapping[str, Any],
    live_by_id: Mapping[str, Mapping[str, Any]],
    tenant_id: str,
    workspace_id: str,
) -> dict[str, ExperienceAction]:
    template = ACTION_TEMPLATES[EXECUTABLE_TEMPLATE_ID]

    async def _issue(
        conn: Any, scoped_tenant_id: str | None, scoped_workspace_id: str
    ) -> dict[str, ExperienceAction]:
        if scoped_tenant_id != tenant_id or scoped_workspace_id != workspace_id:
            return {}
        authorization = await _capture(conn, user, tenant_id, workspace_id)
        if authorization is None:
            return {}
        await require_enabled_action_template(conn, EXECUTABLE_TEMPLATE_ID)
        rows = await fetch_authoritative_rows(
            conn,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            item_ids=tuple(live_by_id),
        )
        issued: dict[str, ExperienceAction] = {}
        for item_id, live in live_by_id.items():
            row = rows.get(item_id)
            if row is None:
                continue
            contract = match_authoritative_item(live, row, authorization, template)
            if contract is None:
                continue
            token = await insert_action_binding_token(conn, contract)
            if token is None:
                continue
            handle, _expires_at = token
            issued[item_id] = public_action(handle)
        return issued

    pool = await auth.pool()
    return await run_with_db_scope(pool, dict(user), _issue)


def _ordered(actions: Mapping[str, ExperienceAction]) -> tuple[ExperienceAction, ...]:
    return tuple(
        actions[template_id]
        for template_id in BINDING_TEMPLATE_ORDER
        if template_id in actions
    )


async def _guarded(operation: Any) -> Any:
    try:
        return await operation
    except HTTPException as exc:
        if exc.status_code >= 500:
            _record_operational_failure()
    except Exception:
        _record_operational_failure()
    return {}


async def issue_action_bindings(
    user: Mapping[str, Any],
    snapshot: SurfaceSnapshot,
    *,
    enabled_template_ids: Collection[str],
) -> dict[str, tuple[ExperienceAction, ...]]:
    try:
        require_write(user)
        tenant_id, workspace_id = authority_scope(user)
    except HTTPException:
        return {}
    direct = [
        template_id
        for template_id in LIVE_DIRECT_ORDER
        if template_id in enabled_template_ids
    ]
    followups = EXECUTABLE_TEMPLATE_ID in enabled_template_ids
    if not direct and not followups:
        return {}
    live_by_id = {
        str(item.get("id") or item.get("item_id")): item
        for item in snapshot.items
        if str(item.get("id") or item.get("item_id") or "").strip()
    }
    if not live_by_id:
        return {}

    issued: dict[str, dict[str, ExperienceAction]] = {}
    if direct:
        by_item = await _guarded(
            _issue_direct(user, live_by_id, direct, tenant_id, workspace_id)
        )
        for item_id, actions in by_item.items():
            issued.setdefault(item_id, {}).update(actions)
    if followups:
        followup_actions = await _guarded(
            _issue_followups(user, live_by_id, tenant_id, workspace_id)
        )
        for item_id, action in followup_actions.items():
            issued.setdefault(item_id, {})[EXECUTABLE_TEMPLATE_ID] = action
    return {
        item_id: _ordered(actions) for item_id, actions in issued.items() if actions
    }


async def issue_reopen_bindings(
    user: Mapping[str, Any],
    item_ids: Iterable[str],
    *,
    enabled_template_ids: Collection[str],
) -> dict[str, ExperienceAction]:
    try:
        require_write(user)
        tenant_id, workspace_id = authority_scope(user)
    except HTTPException:
        return {}
    ids = sorted({str(value) for value in item_ids if str(value).strip()})
    if REOPEN_EXCEPTION_TEMPLATE_ID not in enabled_template_ids or not ids:
        return {}
    template = ACTION_TEMPLATES[REOPEN_EXCEPTION_TEMPLATE_ID]

    async def _issue(
        conn: Any, scoped_tenant_id: str | None, scoped_workspace_id: str
    ) -> dict[str, ExperienceAction]:
        if scoped_tenant_id != tenant_id or scoped_workspace_id != workspace_id:
            return {}
        authorization = await _capture(conn, user, tenant_id, workspace_id)
        if authorization is None:
            return {}
        if REOPEN_EXCEPTION_TEMPLATE_ID not in await _enabled_now(
            conn, (REOPEN_EXCEPTION_TEMPLATE_ID,)
        ):
            return {}
        rows = await fetch_direct_rows(
            conn, tenant_id=tenant_id, workspace_id=workspace_id, item_ids=ids
        )
        contracts = [
            contract
            for item_id in ids
            if (row := rows.get(item_id)) is not None
            and (contract := match_reopen_item(row, authorization, template, user=user))
            is not None
        ]
        handles = await _slot_handles(conn, contracts)
        return {
            contract.item_id: public_action(
                handle, template_id=REOPEN_EXCEPTION_TEMPLATE_ID
            )
            for contract in contracts
            if (handle := handles.get((contract.item_id, REOPEN_EXCEPTION_TEMPLATE_ID)))
            is not None
        }

    async def _run() -> dict[str, ExperienceAction]:
        pool = await auth.pool()
        return await run_with_db_scope(pool, dict(user), _issue)

    return await _guarded(_run())


__all__ = (
    "NEEDS_REFRESH_REASON",
    "issue_action_bindings",
    "issue_reopen_bindings",
    "placeholder_handle",
    "refresh_required_action",
)
