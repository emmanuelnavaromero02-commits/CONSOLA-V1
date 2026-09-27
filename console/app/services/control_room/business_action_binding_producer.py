from __future__ import annotations

import logging
from collections.abc import Collection, Mapping
from typing import Any

from fastapi import HTTPException

from app.schemas.control_room_experience_actions import ExperienceAction
from app.services import auth
from app.services.control_room.business_action_authoritative_item import (
    match_authoritative_item,
)
from app.services.control_room.business_action_authority_policy import (
    BINDING_TEMPLATE_ORDER,
    EXECUTABLE_TEMPLATE_ID,
    authority_scope,
    require_write,
)
from app.services.control_room.business_action_authority_repository import (
    fetch_authoritative_rows,
    fetch_direct_rows,
    insert_action_binding_token,
    insert_direct_action_binding_token,
)
from app.services.control_room.business_action_catalog import (
    require_enabled_action_template,
)
from app.services.control_room.business_action_direct_contract import (
    match_direct_action_item,
)
from app.services.control_room.business_action_public_projection import public_action
from app.services.control_room.business_action_registry import ACTION_TEMPLATES
from app.services.control_room.business_action_authorization_snapshot import (
    capture_authorization_snapshot,
)
from app.services.control_room.surface_snapshot import SurfaceSnapshot
from app.services.db_scope import run_with_db_scope


_LOGGER = logging.getLogger(__name__)
_OPERATIONAL_FAILURE = "control_room_action_binding_operational_failure"


def _record_operational_failure() -> None:
    _LOGGER.error(
        _OPERATIONAL_FAILURE,
        extra={
            "event": _OPERATIONAL_FAILURE,
            "component": "control_room_action_authority",
            "outcome": "actions_omitted",
        },
    )


async def _issue_template(
    user: Mapping[str, Any],
    live_by_id: Mapping[str, Mapping[str, Any]],
    *,
    template_id: str,
    tenant_id: str,
    workspace_id: str,
) -> dict[str, ExperienceAction]:
    template = ACTION_TEMPLATES[template_id]

    async def _issue(
        conn: Any, scoped_tenant_id: str | None, scoped_workspace_id: str
    ) -> dict[str, ExperienceAction]:
        if scoped_tenant_id != tenant_id or scoped_workspace_id != workspace_id:
            return {}
        authorization = await capture_authorization_snapshot(
            conn,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_user_id=int(user["id"]),
            permission="control_room.write",
        )
        if authorization is None:
            return {}
        await require_enabled_action_template(conn, template_id)
        fetch = (
            fetch_authoritative_rows
            if template_id == EXECUTABLE_TEMPLATE_ID
            else fetch_direct_rows
        )
        rows = await fetch(
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
            if template_id == EXECUTABLE_TEMPLATE_ID:
                contract = match_authoritative_item(live, row, authorization, template)
                if contract is None:
                    continue
                token = await insert_action_binding_token(conn, contract)
                if token is None:
                    continue
            else:
                direct = match_direct_action_item(
                    live, row, authorization, template, user=user
                )
                if direct is None:
                    continue
                token = await insert_direct_action_binding_token(conn, direct)
            handle, _expires_at = token
            issued[item_id] = public_action(handle, template_id=template_id)
        return issued

    pool = await auth.pool()
    return await run_with_db_scope(pool, dict(user), _issue)


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
    template_ids = [
        template_id
        for template_id in BINDING_TEMPLATE_ORDER
        if template_id in enabled_template_ids
    ]
    if not template_ids:
        return {}
    live_by_id = {
        str(item.get("id") or item.get("item_id")): item
        for item in snapshot.items
        if str(item.get("id") or item.get("item_id") or "").strip()
    }
    if not live_by_id:
        return {}

    issued: dict[str, list[ExperienceAction]] = {}
    for template_id in template_ids:
        try:
            by_item = await _issue_template(
                user,
                live_by_id,
                template_id=template_id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
            )
        except HTTPException as exc:
            if exc.status_code >= 500:
                _record_operational_failure()
            continue
        except Exception:
            _record_operational_failure()
            continue
        for item_id, action in by_item.items():
            issued.setdefault(item_id, []).append(action)
    return {item_id: tuple(actions) for item_id, actions in issued.items()}


__all__ = ("issue_action_bindings",)
