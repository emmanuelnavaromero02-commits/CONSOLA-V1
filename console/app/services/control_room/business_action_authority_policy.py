from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException

from app.services.control_room.business_action_digest import action_contract_digest
from app.services.permissions import has_permission


EXECUTABLE_TEMPLATE_ID = "create_followup_task"
EXECUTABLE_TEMPLATE_IDS = frozenset({EXECUTABLE_TEMPLATE_ID})
ACTION_BINDING_TTL_SECONDS = 15 * 60
INTENT_TTL_SECONDS = 24 * 60 * 60
EXECUTION_HANDLE_TTL_SECONDS = 5 * 60


def actor_id(user: Mapping[str, Any]) -> int:
    value = user.get("id")
    if isinstance(value, bool):
        raise HTTPException(403, "action authority is unavailable")
    try:
        resolved = int(value)
    except (TypeError, ValueError):
        raise HTTPException(403, "action authority is unavailable") from None
    if resolved <= 0:
        raise HTTPException(403, "action authority is unavailable")
    return resolved


def authority_scope(user: Mapping[str, Any]) -> tuple[str, str]:
    tenant = str(user.get("active_tenant_id") or user.get("tenant_id") or "").strip()
    workspace = str(
        user.get("active_workspace_id") or user.get("workspace_id") or ""
    ).strip()
    if not tenant or not workspace:
        raise HTTPException(403, "action authority is unavailable")
    return tenant, workspace


def _require(user: Mapping[str, Any], permission: str) -> None:
    actor_id(user)
    authority_scope(user)
    if not has_permission(dict(user), permission):
        raise HTTPException(403, "action authority is unavailable")


def require_write(user: Mapping[str, Any]) -> None:
    _require(user, "control_room.write")


def require_approve(user: Mapping[str, Any]) -> None:
    _require(user, "control_room.approve")


def require_execute(user: Mapping[str, Any]) -> None:
    _require(user, "control_room.execute")


def require_distinct_actors(maker_user_id: int, checker_user_id: int) -> None:
    if int(maker_user_id) == int(checker_user_id):
        raise HTTPException(403, "action authority is unavailable")


def decision_contract_digest(workspace_id: str, decision_id: object) -> str:
    if isinstance(decision_id, bool):
        raise ValueError("action authority decision is invalid")
    try:
        resolved = int(decision_id)
    except (TypeError, ValueError):
        raise ValueError("action authority decision is invalid") from None
    if resolved <= 0:
        raise ValueError("action authority decision is invalid")
    return action_contract_digest(
        {"workspace_id": str(workspace_id), "decision_id": resolved}
    )


__all__ = (
    "ACTION_BINDING_TTL_SECONDS",
    "EXECUTABLE_TEMPLATE_ID",
    "EXECUTABLE_TEMPLATE_IDS",
    "EXECUTION_HANDLE_TTL_SECONDS",
    "INTENT_TTL_SECONDS",
    "actor_id",
    "authority_scope",
    "decision_contract_digest",
    "require_approve",
    "require_distinct_actors",
    "require_execute",
    "require_write",
)
