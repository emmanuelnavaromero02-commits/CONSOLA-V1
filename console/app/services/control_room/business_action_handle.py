from __future__ import annotations

from collections.abc import Mapping
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
    resolve_action_binding_token,
)
from app.services.control_room.business_action_preview_capability import (
    install_preview_authority,
)
from app.services.control_room.business_action_registry import ACTION_TEMPLATES
from app.services.control_room.business_explicit_action_binding import (
    verified_explicit_action_bindings,
)
from app.services.control_room.surface_snapshot import collect_surface_snapshot
from app.services.control_room.business_action_authority_policy import (
    EXECUTABLE_TEMPLATE_ID,
    authority_scope,
)
from app.services.db_scope import run_with_db_scope


@dataclass(frozen=True)
class ResolvedActionHandle:
    item_id: str = field(repr=False)
    template_id: str
    binding_id: str = field(repr=False)


async def resolve_business_action_handle(
    user: Mapping[str, Any], action_handle: str
) -> ResolvedActionHandle:
    snapshot = await collect_surface_snapshot(user)
    enabled = await load_enabled_action_template_ids(user)
    if any(
        binding.binding_id == action_handle
        for item in snapshot.items
        for binding in verified_explicit_action_bindings(item)
    ):
        raise HTTPException(404, "action binding not found")

    tenant_id, workspace_id = authority_scope(user)
    pool = await auth.pool()

    async def _resolve(conn: Any, scoped_tenant: str | None, scoped_workspace: str):
        if scoped_tenant != tenant_id or scoped_workspace != workspace_id:
            raise HTTPException(404, "action binding not found")
        token = await resolve_action_binding_token(
            conn,
            user=user,
            action_handle=action_handle,
        )
        item_id = str(token.get("item_id") or "")
        item = next(
            (
                candidate
                for candidate in snapshot.items
                if str(candidate.get("id") or candidate.get("item_id") or "") == item_id
            ),
            None,
        )
        if item is None or EXECUTABLE_TEMPLATE_ID not in enabled:
            raise HTTPException(404, "action binding not found")
        rows = await fetch_authoritative_rows(
            conn,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            item_ids=(item_id,),
        )
        row = rows.get(item_id)
        contract = (
            match_authoritative_item(
                item,
                row,
                user,
                ACTION_TEMPLATES[EXECUTABLE_TEMPLATE_ID],
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
            raise HTTPException(404, "action binding not found")
        return token

    token = await run_with_db_scope(pool, dict(user), _resolve)
    install_preview_authority(token, action_handle)
    return ResolvedActionHandle(
        item_id=str(token["item_id"]),
        template_id=EXECUTABLE_TEMPLATE_ID,
        binding_id=action_handle,
    )


__all__ = ("ResolvedActionHandle", "resolve_business_action_handle")
