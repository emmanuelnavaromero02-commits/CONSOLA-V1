from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException

from app.services.control_room.business_action_catalog import (
    load_enabled_action_template_ids,
)
from app.services import auth
from app.services.control_room.business_action_authoritative_item import (
    match_authoritative_item,
)
from app.services.control_room.business_action_authority_repository import (
    fetch_authoritative_rows,
    fetch_direct_rows,
    resolve_action_binding_token,
)
from app.services.control_room.business_action_direct_contract import (
    match_direct_action_item,
    match_reopen_item,
)
from app.services.control_room.business_action_preview_capability import (
    install_preview_authority,
)
from app.services.control_room.business_action_authorization_binding import (
    token_authorization_matches,
)
from app.services.control_room.business_action_authorization_snapshot import (
    capture_authorization_snapshot,
)
from app.services.control_room.business_action_registry import ACTION_TEMPLATES
from app.services.control_room.business_explicit_action_binding import (
    verified_explicit_action_bindings,
)
from app.services.control_room.surface_snapshot import collect_surface_snapshot
from app.services.control_room.business_action_authority_policy import (
    DIRECT_ACTION_TEMPLATE_IDS,
    EXECUTABLE_TEMPLATE_ID,
    REOPEN_EXCEPTION_TEMPLATE_ID,
    authority_scope,
)
from app.services.db_scope import run_with_db_scope


_PREVIEW_ONLY = frozenset({EXECUTABLE_TEMPLATE_ID})
_RECORD_BOUND = frozenset({REOPEN_EXCEPTION_TEMPLATE_ID})


@dataclass(frozen=True)
class ResolvedActionHandle:
    item_id: str = field(repr=False)
    template_id: str
    binding_id: str = field(repr=False)
    cartridge_id: str | None = field(default=None, repr=False)


def _not_found() -> HTTPException:
    return HTTPException(404, "action binding not found")


async def resolve_business_action_handle(
    user: Mapping[str, Any],
    action_handle: str,
    *,
    allowed_template_ids: Collection[str] = _PREVIEW_ONLY,
) -> ResolvedActionHandle:
    allowed = frozenset(allowed_template_ids) & (
        _PREVIEW_ONLY | DIRECT_ACTION_TEMPLATE_IDS
    )
    if not allowed:
        raise _not_found()
    needs_live = bool(allowed - _RECORD_BOUND)
    snapshot = await collect_surface_snapshot(user) if needs_live else None
    enabled = await load_enabled_action_template_ids(user)
    if snapshot is not None and any(
        binding.binding_id == action_handle
        for item in snapshot.items
        for binding in verified_explicit_action_bindings(item)
    ):
        raise _not_found()

    tenant_id, workspace_id = authority_scope(user)
    pool = await auth.pool()

    async def _resolve(conn: Any, scoped_tenant: str | None, scoped_workspace: str):
        if scoped_tenant != tenant_id or scoped_workspace != workspace_id:
            raise _not_found()
        authorization = await capture_authorization_snapshot(
            conn,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_user_id=int(user.get("id") or 0),
            permission="control_room.write",
        )
        if authorization is None:
            raise _not_found()
        token = await resolve_action_binding_token(
            conn,
            user=user,
            action_handle=action_handle,
        )
        if not token_authorization_matches(token, authorization):
            raise _not_found()
        template_id = str(token.get("template_id") or "")
        if template_id not in allowed or template_id not in enabled:
            raise _not_found()
        item_id = str(token.get("item_id") or "")
        template = ACTION_TEMPLATES[template_id]
        if template_id in _RECORD_BOUND:
            rows = await fetch_direct_rows(
                conn,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                item_ids=(item_id,),
            )
            row = rows.get(item_id)
            contract = (
                match_reopen_item(row, authorization, template, user=user)
                if row is not None
                else None
            )
        else:
            item = next(
                (
                    candidate
                    for candidate in (snapshot.items if snapshot else ())
                    if str(candidate.get("id") or candidate.get("item_id") or "")
                    == item_id
                ),
                None,
            )
            if item is None:
                raise _not_found()
            if template_id == EXECUTABLE_TEMPLATE_ID:
                rows = await fetch_authoritative_rows(
                    conn,
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    item_ids=(item_id,),
                )
                row = rows.get(item_id)
                contract = (
                    match_authoritative_item(item, row, authorization, template)
                    if row is not None
                    else None
                )
            else:
                rows = await fetch_direct_rows(
                    conn,
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    item_ids=(item_id,),
                )
                row = rows.get(item_id)
                contract = (
                    match_direct_action_item(
                        item, row, authorization, template, user=user
                    )
                    if row is not None
                    else None
                )
        expected = {
            "binding_digest": contract.binding_digest if contract else "",
            "evidence_digest": contract.evidence_digest if contract else "",
            "observation_fingerprint": contract.observation_fingerprint
            if contract
            else "",
            "contract_digest": contract.contract_digest if contract else "",
            "target_digest": contract.target_digest if contract else "",
            "decision_digest": contract.decision_digest if contract else "",
        }
        if contract is None or any(
            str(token.get(key) or "") != value for key, value in expected.items()
        ):
            raise _not_found()
        return token, getattr(contract, "cartridge_id", None)

    token, cartridge_id = await run_with_db_scope(pool, dict(user), _resolve)
    template_id = str(token["template_id"])
    if template_id == EXECUTABLE_TEMPLATE_ID:
        install_preview_authority(token, action_handle)
    return ResolvedActionHandle(
        item_id=str(token["item_id"]),
        template_id=template_id,
        binding_id=action_handle,
        cartridge_id=cartridge_id,
    )


__all__ = ("ResolvedActionHandle", "resolve_business_action_handle")
