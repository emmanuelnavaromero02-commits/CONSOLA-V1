from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException


TERMINAL_WORKFLOW_STATUSES = frozenset({"approved", "dismissed", "resolved"})


def require_nonterminal_workflow(item: Mapping[str, Any], *, operation: str) -> None:
    status = str(item.get("status") or "open").strip().lower()
    if status not in TERMINAL_WORKFLOW_STATUSES:
        return
    raise HTTPException(
        409,
        {
            "code": "terminal_item",
            "message": f"terminal control room item cannot {operation}",
            "status": status,
        },
    )


async def lock_nonterminal_workflow_item(
    conn: Any,
    *,
    workspace_id: str,
    item_id: str,
    owner_user_id: int | None,
    operation: str,
    allow_missing: bool = False,
) -> dict[str, Any] | None:
    row = await conn.fetchrow(
        """
        SELECT item_id, status
          FROM control_room_items
         WHERE workspace_id = $1
           AND item_id = $2
           AND owner_user_id IS NOT DISTINCT FROM $3
         FOR UPDATE
        """,
        workspace_id,
        item_id,
        owner_user_id,
    )
    if not row:
        if allow_missing:
            return None
        raise HTTPException(404, "control room item not found")
    locked = dict(row)
    require_nonterminal_workflow(locked, operation=operation)
    return locked


__all__ = (
    "TERMINAL_WORKFLOW_STATUSES",
    "lock_nonterminal_workflow_item",
    "require_nonterminal_workflow",
)
