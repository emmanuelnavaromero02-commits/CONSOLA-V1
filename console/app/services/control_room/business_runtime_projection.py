from __future__ import annotations

from typing import Any

from app.services.control_room.business_access import (
    can_read_workspace_wide,
    owner_scope_id,
    workspace_scope,
)
from app.services.control_room.business_item_reader import (
    fetch_eligible_persisted_items,
)
from app.services.control_room.business_cartridge_scope import (
    filter_business_cartridge_items,
)
from app.services.control_room.business_projection import (
    normalize_persisted_business_item,
)
from app.services.db_scope import run_with_db_scope


async def persisted_business_projection(
    pool: Any,
    user: dict | None,
    *,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    tenant_id, workspace_id = workspace_scope(user)
    owner_id = owner_scope_id(user)
    if not can_read_workspace_wide(user) and owner_id is None:
        return []

    async def _load(
        conn: Any,
        _tenant_id: str | None,
        _workspace_id: str,
    ) -> list[dict[str, Any]]:
        return await fetch_eligible_persisted_items(
            conn,
            workspace_id=workspace_id,
            tenant_id=tenant_id,
            owner_id=owner_id,
            kinds=(),
            row_to_item=normalize_persisted_business_item,
            limit=limit,
            page_size=250,
        )

    items = await run_with_db_scope(pool, user or {}, _load)
    return filter_business_cartridge_items(items, user)


__all__ = ("persisted_business_projection",)
