"""Pure access helpers for decision routes."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def current_workspace_id(user: Mapping[str, Any]) -> str | None:
    return user.get("active_workspace_id") or user.get("workspace_id")


def is_decision_workspace_admin(*, is_global_admin: bool, workspace_role: str | None) -> bool:
    return is_global_admin or workspace_role in {"workspace_admin", "tenant_admin"}


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

