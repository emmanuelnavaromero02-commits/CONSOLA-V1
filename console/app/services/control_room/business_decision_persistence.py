from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from fastapi import HTTPException

from app.services.control_room.business_access import expected_item_owner
from app.services.control_room.business_repository import (
    decision_provenance,
    link_control_room_decision,
)
from app.services.control_room.business_terminal_workflow_guard import (
    require_nonterminal_workflow,
)
from app.services.control_room.business_workflow_provenance import (
    WorkflowStage,
    workflow_has_eligible_provenance,
)
from app.services.control_room.business_workflow_stage_cas import (
    select_option_with_stage_cas,
)
from app.services.control_room.business_workflow_quarantine import (
    workflow_columns_unlinked,
    workflow_is_quarantined,
)


ItemWriter = Callable[..., Awaitable[None]]


def _decision_fields(
    item: Mapping[str, Any],
) -> tuple[str, str, list[dict[str, Any]]]:
    title = f"{item['title']} - {item['entity_label']}"
    description = (
        f"{item['description']}\n\n"
        f"Recomendacion OMEGA: {item['recommendation']}\n\n"
        f"Fuente: {item['source_dataset']} ({item['cartridge']})."
    )
    kpis = [
        {
            "label": "Severidad",
            "value": item["severity"],
            "source": item["source_dataset"],
        },
        {
            "label": "Entidad",
            "value": item["entity_label"],
            "source": item["cartridge"],
        },
        {
            "label": "Estado OMEGA",
            "value": item.get("status") or "open",
            "source": "control_room",
        },
        decision_provenance("control_room", item_id=str(item["id"])),
    ]
    return title, description, kpis


async def create_and_link_decision(
    conn: Any,
    *,
    user: Mapping[str, Any],
    item: Mapping[str, Any],
    workspace_id: str,
    ensure_item_row: ItemWriter,
    record_item_event: ItemWriter,
) -> Any:
    owner_user_id = expected_item_owner(item, user)
    existing_state = await conn.fetchrow(
        """SELECT item_id, status
             FROM control_room_items
            WHERE workspace_id = $1
              AND item_id = $2
              AND owner_user_id IS NOT DISTINCT FROM $3
            FOR UPDATE""",
        workspace_id,
        item["id"],
        owner_user_id,
    )
    require_nonterminal_workflow(
        existing_state or item, operation="create or link a decision"
    )
    initial_item = {
        **dict(item),
        "decision_id": None,
        "selected_option_id": None,
        "execution_status": "not_started",
    }
    await ensure_item_row(
        conn,
        user=dict(user),
        item=initial_item,
        status=str(item.get("status") or "open"),
        critical=True,
        allow_diagnostic_transition=True,
    )
    locked = await conn.fetchrow(
        """SELECT item_id, decision_id, selected_option_id, owner_user_id, metadata,
                  status, execution_status
             FROM control_room_items
            WHERE workspace_id = $1
              AND item_id = $2
              AND owner_user_id IS NOT DISTINCT FROM $3
            FOR UPDATE""",
        workspace_id,
        item["id"],
        owner_user_id,
    )
    if not locked:
        raise HTTPException(404, "control room item not found")
    require_nonterminal_workflow(locked, operation="create or link a decision")
    locked_item = {
        **dict(item),
        **dict(locked),
        "workspace_id": workspace_id,
    }
    if workflow_is_quarantined(locked_item):
        raise HTTPException(409, "control room item has quarantined workflow")
    existing_decision_id = locked.get("decision_id")
    if existing_decision_id is not None:
        scoped_item = {
            **dict(item),
            "workspace_id": workspace_id,
            "selected_option_id": locked.get("selected_option_id"),
        }
        if not workflow_has_eligible_provenance(
            locked.get("metadata"),
            scoped_item,
            decision_id=existing_decision_id,
            use_stored_fingerprint=True,
        ):
            raise HTTPException(409, "control room item has quarantined workflow")
        existing = await conn.fetchrow(
            "SELECT * FROM decisions WHERE id = $1 AND workspace_id = $2",
            existing_decision_id,
            workspace_id,
        )
        if not existing:
            raise HTTPException(409, "control room decision link is invalid")
        return existing

    decision_item = {
        **dict(item),
        "selected_option_id": locked.get("selected_option_id"),
    }
    valid_option_stage = bool(
        locked.get("selected_option_id")
        and str(locked.get("execution_status") or "not_started") == "not_started"
        and str(locked.get("status") or "open") in {"open", "in_review"}
        and workflow_has_eligible_provenance(
            locked.get("metadata"),
            decision_item,
            decision_id=None,
            allowed_stages=(WorkflowStage.OPTION_SELECTED,),
        )
    )
    if (
        locked.get("metadata")
        and not workflow_columns_unlinked(locked)
        and not valid_option_stage
    ):
        raise HTTPException(409, "previous control room workflow is not unlinked")

    title, description, kpis = _decision_fields(item)
    row = await conn.fetchrow(
        """INSERT INTO decisions
              (title, description, commitment_date, kpis, created_by_id, assignee_id, visibility, workspace_id)
           VALUES ($1, $2, CURRENT_DATE + 7, $3::jsonb, $4, NULL, 'shared', $5)
           RETURNING *""",
        title,
        description,
        json.dumps(kpis),
        user["id"],
        workspace_id,
    )
    await conn.fetchrow(
        """INSERT INTO decision_actions (decision_id, action_text, note, actor)
           VALUES ($1, $2, $3, $4)
           RETURNING *""",
        row["id"],
        "Decision creada desde Sala de Control",
        item["recommendation"],
        user.get("email") or "user",
    )
    await link_control_room_decision(
        conn,
        workspace_id=workspace_id,
        item_id=str(item["id"]),
        decision_id=row["id"],
        owner_user_id=owner_user_id,
        item=decision_item,
    )
    await record_item_event(
        conn,
        user=dict(user),
        item=dict(item),
        event_type="decision_created",
        metadata={"decision_id": row["id"]},
    )
    return row


async def persist_option_selection(
    conn: Any,
    *,
    user: Mapping[str, Any],
    item: Mapping[str, Any],
    workspace_id: str,
    option_id: str,
    terminal_statuses: Sequence[str],
    ensure_item_row: ItemWriter,
    record_item_event: ItemWriter,
) -> None:
    await select_option_with_stage_cas(
        conn,
        user=user,
        item=item,
        workspace_id=workspace_id,
        option_id=option_id,
        terminal_statuses=terminal_statuses,
        ensure_item_row=ensure_item_row,
    )
    await record_item_event(
        conn,
        user=dict(user),
        item=dict(item),
        event_type="option_selected",
        metadata={"option_id": option_id},
    )
