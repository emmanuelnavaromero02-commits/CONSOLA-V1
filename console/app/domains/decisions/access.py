from __future__ import annotations

# fmt: off

from collections.abc import Mapping
from typing import Any


def current_workspace_id(user: Mapping[str, Any]) -> str | None:
    return user.get("active_workspace_id") or user.get("workspace_id")


def is_decision_workspace_admin(*, is_global_admin: bool, workspace_role: str | None) -> bool:
    return is_global_admin or workspace_role in {"workspace_admin", "tenant_admin"}


def decision_visible_clause(
    *,
    uid: int,
    is_admin: bool,
    params: list[Any],
    workspace_id: str | None,
) -> str:
    if not workspace_id:
        return "FALSE"
    params.append(workspace_id)
    ws_param = f"${len(params)}"
    workspace_clause = f"workspace_id = {ws_param}"
    if is_admin:
        return workspace_clause
    params.append(uid)
    p = f"${len(params)}"
    return f"({workspace_clause} AND (created_by_id = {p} OR assignee_id = {p}))"


def decision_load_query(
    *,
    decision_id: int,
    user_id: int,
    is_admin: bool,
    workspace_id: str,
) -> tuple[str, list[Any]]:
    params: list[Any] = [decision_id, workspace_id]
    sql = "SELECT * FROM decisions WHERE id = $1 AND workspace_id = $2"
    if not is_admin:
        params.append(user_id)
        sql += f" AND (created_by_id = ${len(params)} OR assignee_id = ${len(params)})"
    return sql, params


def decision_list_query(
    *,
    user_id: int,
    is_admin: bool,
    workspace_id: str,
    status: str,
    overdue: str,
) -> tuple[str, list[Any]]:
    where: list[str] = []
    params: list[Any] = []
    where.append(
        decision_visible_clause(
            uid=user_id,
            is_admin=is_admin,
            params=params,
            workspace_id=workspace_id,
        )
    )
    if status in ("open", "closed"):
        params.append(status)
        where.append(f"status = ${len(params)}")
    if overdue.lower() == "true":
        where.append(
            "status = 'open' AND commitment_date IS NOT NULL AND commitment_date < CURRENT_DATE"
        )
    sql = "SELECT * FROM decisions WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC, id DESC"
    return sql, params


def can_edit_decision(
    row: Mapping[str, Any],
    user: Mapping[str, Any],
    *,
    is_workspace_admin: bool,
) -> bool:
    if is_workspace_admin:
        return True
    return row.get("created_by_id") == user["id"] or row.get("assignee_id") == user["id"]


def can_delete_decision(
    row: Mapping[str, Any],
    user: Mapping[str, Any],
    *,
    is_workspace_admin: bool,
) -> bool:
    if is_workspace_admin:
        return True
    return row.get("created_by_id") == user["id"]
