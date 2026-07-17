from __future__ import annotations

from typing import Any

from app.domains.decisions.business_visibility import count_business_decisions
from app.services.control_room.business_access import (
    can_read_workspace_wide,
    owner_scope_id,
    workspace_scope,
)
from app.services.db_scope import run_with_db_scope


async def count_open_business_decisions(
    pool: Any,
    user: dict | None,
) -> int:
    tenant_id, workspace_id = workspace_scope(user)
    params: list[Any] = [workspace_id]
    clauses = ["workspace_id = $1", "status = 'open'"]
    owner_id = owner_scope_id(user)
    if not can_read_workspace_wide(user):
        if owner_id is None:
            return 0
        params.append(owner_id)
        clauses.append(
            f"(created_by_id = ${len(params)} OR assignee_id = ${len(params)})"
        )
    sql = (
        "SELECT * FROM decisions WHERE "
        + " AND ".join(clauses)
        + " ORDER BY created_at DESC, id DESC"
    )

    async def _count(
        conn: Any,
        _tenant_id: str | None,
        _workspace_id: str,
    ) -> int:
        return await count_business_decisions(
            conn,
            sql=sql,
            params=params,
            workspace_id=workspace_id,
            tenant_id=tenant_id,
            owner_id=owner_id,
        )

    return await run_with_db_scope(pool, user or {}, _count)


__all__ = ("count_open_business_decisions",)
