from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from fastapi import HTTPException

from app.services.control_room.business_access import expected_item_owner
from app.services.control_room.business_mutation_guard import (
    lock_authoritative_business_item,
)
from app.services.control_room.business_serialization import dumps_jsonb
from app.services.control_room.business_workflow_provenance import (
    DECISION_PROVENANCE_KEY,
    WorkflowStage,
    workflow_eligibility_provenance,
)


ItemWriter = Callable[..., Awaitable[None]]
_IMMUTABLE_STAGES = frozenset({WorkflowStage.APPROVED, WorkflowStage.EXECUTED})


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


def _stage(row: Mapping[str, Any]) -> WorkflowStage | None:
    provenance = _metadata(row.get("metadata")).get(DECISION_PROVENANCE_KEY)
    if not isinstance(provenance, Mapping):
        return None
    try:
        return WorkflowStage(str(provenance.get("stage") or "").strip())
    except ValueError:
        return None


async def _lock_option_item(
    conn: Any,
    *,
    user: Mapping[str, Any],
    item: Mapping[str, Any],
    decision_id: int | None,
    ensure_item_row: ItemWriter,
) -> dict[str, Any]:
    allowed = (
        (WorkflowStage.DECISION_CREATED,)
        if decision_id is not None
        else (WorkflowStage.OPTION_SELECTED,)
    )
    locked = await lock_authoritative_business_item(
        conn,
        user=user,
        item=item,
        allow_missing=True,
        decision_id=decision_id,
        allowed_stages=allowed,
    )
    if locked is None:
        await ensure_item_row(
            conn,
            user=dict(user),
            item=dict(item),
            status="in_review",
        )
        locked = await lock_authoritative_business_item(
            conn,
            user=user,
            item=item,
            decision_id=decision_id,
            allowed_stages=allowed,
        )
    if locked is None:
        raise HTTPException(404, "control room item not found")
    return locked


async def select_option_with_stage_cas(
    conn: Any,
    *,
    user: Mapping[str, Any],
    item: Mapping[str, Any],
    workspace_id: str,
    option_id: str,
    terminal_statuses: Sequence[str],
    ensure_item_row: ItemWriter,
) -> None:
    decision_id = (
        int(item["decision_id"]) if item.get("decision_id") is not None else None
    )
    locked = await _lock_option_item(
        conn,
        user=user,
        item=item,
        decision_id=decision_id,
        ensure_item_row=ensure_item_row,
    )
    terminal = {str(value) for value in terminal_statuses} | {
        "approved",
        "executed",
        "resolved",
        "dismissed",
    }
    if (
        str(locked.get("status") or "") in terminal
        or _stage(locked) in _IMMUTABLE_STAGES
    ):
        raise HTTPException(409, "terminal control room workflow cannot change option")
    provenance_stage = (
        WorkflowStage.DECISION_CREATED
        if decision_id is not None
        else WorkflowStage.OPTION_SELECTED
    )
    provenance = workflow_eligibility_provenance(
        {**dict(item), "workspace_id": workspace_id},
        stage=provenance_stage,
        workspace_id=workspace_id,
        decision_id=decision_id,
        option_id=option_id,
    )
    updated = await conn.fetchrow(
        """
        UPDATE control_room_items
           SET status = CASE
                   WHEN decision_id IS NULL THEN 'in_review'
                   ELSE status
               END,
               metadata = COALESCE(metadata, '{}'::jsonb) || $1::jsonb,
               selected_option_id = $4,
               last_seen_at = NOW()
         WHERE workspace_id = $2
           AND item_id = $3
           AND owner_user_id IS NOT DISTINCT FROM $5
           AND status <> ALL($6::text[])
           AND decision_id IS NOT DISTINCT FROM $7
           AND COALESCE(metadata->'decision_eligibility_provenance'->>'stage', '')
               <> ALL($8::text[])
         RETURNING item_id
        """,
        dumps_jsonb(
            {
                "selected_option_id": option_id,
                DECISION_PROVENANCE_KEY: provenance,
            }
        ),
        workspace_id,
        item["id"],
        option_id,
        expected_item_owner(item, user),
        sorted(terminal),
        decision_id,
        sorted(stage.value for stage in _IMMUTABLE_STAGES),
    )
    if not updated or str(updated.get("item_id")) != str(item["id"]):
        raise HTTPException(409, "control room workflow stage changed")


__all__ = ("select_option_with_stage_cas",)
