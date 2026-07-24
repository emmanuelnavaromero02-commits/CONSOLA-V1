from __future__ import annotations

from collections.abc import Callable, Collection, Mapping
from typing import Any

from fastapi import HTTPException

from app.services.control_room.business_access import (
    can_read_workspace_wide,
    expected_item_owner,
    owner_scope_id,
    workspace_scope,
)
from app.services.control_room.business_item_persistence import (
    OwnerScopeConflict,
    ensure_item_row,
)
from app.services.control_room.business_mutation_guard import (
    lock_authoritative_business_item,
)
from app.services.control_room.business_state_rows import ensured_row


async def ensure_authoritative_item_row(
    conn: Any,
    *,
    user: Mapping[str, Any],
    item: Mapping[str, Any],
    status: str,
    allow_diagnostic_transition: bool,
    impact_builder: Callable[[dict[str, Any]], dict[str, Any]],
    metadata_builder: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]],
    terminal_statuses: Collection[str],
) -> None:
    tenant_id, workspace_id = workspace_scope(user)
    await lock_authoritative_business_item(
        conn,
        user=user,
        item=item,
        allow_missing=True,
        allow_diagnostic_transition=allow_diagnostic_transition,
    )
    mutable_item = dict(item)
    impact = impact_builder(mutable_item)
    row = ensured_row(
        mutable_item,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        owner_user_id=expected_item_owner(item, user),
        status=status,
        impact=impact,
        metadata=metadata_builder(mutable_item, impact),
    )
    try:
        await ensure_item_row(
            conn,
            row,
            terminal_statuses=sorted(terminal_statuses),
            owner_scope_id=owner_scope_id(user),
            workspace_wide=can_read_workspace_wide(user),
        )
    except OwnerScopeConflict:
        raise HTTPException(404, "control room item not found") from None


__all__ = ("ensure_authoritative_item_row",)
