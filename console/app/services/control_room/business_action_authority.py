from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from fastapi import HTTPException

from app.services.control_room.business_observation import semantic_states
from app.services.control_room.business_workflow_state import (
    NONTERMINAL_EXECUTION_STATUSES,
)


ActionOperation = Literal["preview", "dry_run", "execute"]
_PREVIEW_ACTION_STATUSES = frozenset({"open", "in_review", "decision_created"})
_EXECUTE_ACTION_STATUSES = frozenset({"approved"})


def action_source_binding_complete(item: Mapping[str, Any]) -> bool:
    return bool(str(item.get("source_dataset") or "").strip()) and bool(
        str(item.get("entity_id") or "").strip()
    )


def action_item_is_current(
    item: Mapping[str, Any],
    *,
    operation: ActionOperation,
) -> bool:
    status = str(item.get("status") or "open").strip().lower()
    execution = str(item.get("execution_status") or "not_started").strip().lower()
    allowed_statuses = {
        "preview": _PREVIEW_ACTION_STATUSES,
        "dry_run": _PREVIEW_ACTION_STATUSES,
        "execute": _EXECUTE_ACTION_STATUSES,
    }[operation]
    return status in allowed_statuses and execution in NONTERMINAL_EXECUTION_STATUSES


def action_item_is_stale(item: Mapping[str, Any]) -> bool:
    return "stale" in semantic_states(item)


def require_action_item_prerequisites(
    item: Mapping[str, Any],
    *,
    operation: ActionOperation,
) -> None:
    if not action_item_is_current(item, operation=operation):
        raise HTTPException(409, "control room item is not actionable")
    if action_item_is_stale(item):
        raise HTTPException(409, "control room item data is stale")
    if not action_source_binding_complete(item):
        raise HTTPException(409, "control room item prerequisites are incomplete")


def require_action_item_evidence(item: Mapping[str, Any]) -> None:
    """Guard source evidence before the authoritative execute lifecycle check."""

    if action_item_is_stale(item):
        raise HTTPException(409, "control room item data is stale")
    if not action_source_binding_complete(item):
        raise HTTPException(409, "control room item prerequisites are incomplete")


__all__ = (
    "ActionOperation",
    "action_item_is_current",
    "action_item_is_stale",
    "action_source_binding_complete",
    "require_action_item_evidence",
    "require_action_item_prerequisites",
)
