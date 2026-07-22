from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException

from app.services.control_room.business_execution_precondition import (
    require_matching_dry_run,
)
from app.services.control_room.business_mutation_guard import (
    lock_authoritative_business_item,
)
from app.services.control_room.business_projection import (
    normalize_persisted_business_item,
)
from app.services.control_room.business_workflow_provenance import (
    WorkflowStage,
    workflow_has_eligible_provenance,
)


@dataclass(frozen=True)
class ExecutionLifecycleBlock:
    code: str
    message: str


def execution_lifecycle_block(
    item: Mapping[str, Any],
) -> ExecutionLifecycleBlock | None:
    if not item.get("decision_id"):
        return ExecutionLifecycleBlock(
            "decision_required",
            "decision is required before execution",
        )
    if str(item.get("status") or "").strip().lower() in {"dismissed", "resolved"}:
        return ExecutionLifecycleBlock(
            "terminal_item",
            "terminal control room item cannot execute supervised action",
        )
    if str(item.get("execution_status") or "not_started").strip().lower() == "executed":
        return ExecutionLifecycleBlock(
            "already_executed",
            "executed control room item cannot execute another action",
        )
    if str(item.get("execution_status") or "not_started").strip().lower() != (
        "dry_run_validated"
    ):
        return ExecutionLifecycleBlock(
            "dry_run_required",
            "dry-run validation is required before execution",
        )
    return None


def _metadata(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return dict(parsed) if isinstance(parsed, Mapping) else {}
    return {}


def _approval_required() -> HTTPException:
    return HTTPException(
        409,
        {
            "code": "workflow_approval_required",
            "message": "the current control room workflow must be explicitly approved",
        },
    )


async def require_approved_execution(
    conn: Any,
    *,
    user: Mapping[str, Any],
    item: Mapping[str, Any],
    template_id: str,
) -> dict[str, Any]:
    row = await lock_authoritative_business_item(
        conn,
        user=user,
        item=item,
        decision_id=int(item["decision_id"]),
        allowed_stages=(WorkflowStage.DECISION_CREATED, WorkflowStage.APPROVED),
    )
    if row is None:
        raise _approval_required()
    normalized = normalize_persisted_business_item(row)
    if str(row.get("status") or "").strip().lower() != "approved" or not (
        workflow_has_eligible_provenance(
            _metadata(row.get("metadata")),
            normalized,
            decision_id=item["decision_id"],
            allowed_stages=(WorkflowStage.APPROVED,),
        )
    ):
        raise _approval_required()
    await require_matching_dry_run(
        conn,
        user=user,
        item=item,
        template_id=template_id,
    )
    return dict(row)


__all__ = (
    "ExecutionLifecycleBlock",
    "execution_lifecycle_block",
    "require_approved_execution",
)
