from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.services.control_room.business_workflow_provenance import WorkflowStage


TERMINAL_EXECUTION_STATUSES = frozenset({"executed", "resolved", "terminal"})
NONTERMINAL_EXECUTION_STATUSES = frozenset(
    {
        "not_started",
        "preview_generated",
        "dry_run_validated",
        "blocked",
        "failed",
    }
)
KNOWN_EXECUTION_STATUSES = TERMINAL_EXECUTION_STATUSES | NONTERMINAL_EXECUTION_STATUSES


def _status(value: Any, *, default: str = "") -> str:
    return str(value or default).strip().lower()


def _decision_linked(row: Mapping[str, Any]) -> bool:
    decision_id = row.get("decision_id")
    return type(decision_id) is int and decision_id > 0


def _option_selected(row: Mapping[str, Any]) -> bool:
    option_id = row.get("selected_option_id")
    return isinstance(option_id, str) and bool(option_id.strip())


def expected_workflow_stage(
    row: Mapping[str, Any],
) -> WorkflowStage | None:

    status = _status(row.get("status"), default="open")
    execution_status = _status(
        row.get("execution_status"),
        default="not_started",
    )
    if execution_status not in KNOWN_EXECUTION_STATUSES:
        return None

    has_decision = _decision_linked(row)
    has_option = _option_selected(row)
    if execution_status in TERMINAL_EXECUTION_STATUSES:
        if has_decision and status in {"approved", "resolved"}:
            return WorkflowStage.EXECUTED
        return None

    if status == "decision_created" and has_decision:
        return WorkflowStage.DECISION_CREATED
    if status == "approved" and has_decision:
        return WorkflowStage.APPROVED
    if status == "resolved" and has_decision and execution_status == "not_started":
        return WorkflowStage.APPROVED
    if (
        status == "in_review"
        and not has_decision
        and has_option
        and execution_status == "not_started"
    ):
        return WorkflowStage.OPTION_SELECTED
    return None


__all__ = (
    "KNOWN_EXECUTION_STATUSES",
    "NONTERMINAL_EXECUTION_STATUSES",
    "TERMINAL_EXECUTION_STATUSES",
    "expected_workflow_stage",
)
