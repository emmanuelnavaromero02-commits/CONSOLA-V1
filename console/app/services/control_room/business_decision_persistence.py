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
from app.services.control_room.business_serialization import dumps_jsonb
from app.services.control_room.business_workflow_provenance import (
    WorkflowStage,
    workflow_eligibility_provenance,
    workflow_has_eligible_provenance,
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
    await ensure_item_row(
        conn,
        user=dict(user),
        item=dict(item),
        status="decision_created",
        critical=True,
    )
    owner_user_id = expected_item_owner(item, user)
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

    if locked.get("metadata") and not workflow_columns_unlinked(locked):
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
        item=item,
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
    await ensure_item_row(
        conn,
        user=dict(user),
        item=dict(item),
        status="in_review",
    )
    decision_id = item.get("decision_id")
    stage = (
        WorkflowStage.DECISION_CREATED
        if decision_id is not None
        else WorkflowStage.OPTION_SELECTED
    )
    provenance = workflow_eligibility_provenance(
        {**dict(item), "workspace_id": workspace_id},
        stage=stage,
        workspace_id=workspace_id,
        decision_id=decision_id,
        option_id=option_id,
    )
    updated = await conn.fetchrow(
        """
        UPDATE control_room_items
           SET status = CASE
                   WHEN status = ANY($4::text[]) THEN status
                   ELSE 'in_review'
               END,
               metadata = COALESCE(metadata, '{}'::jsonb) || $1::jsonb,
               selected_option_id = $5,
               last_seen_at = NOW()
         WHERE workspace_id = $2
           AND item_id = $3
           AND owner_user_id IS NOT DISTINCT FROM $6
         RETURNING item_id
        """,
        dumps_jsonb(
            {
                "selected_option_id": option_id,
                "decision_eligibility_provenance": provenance,
            }
        ),
        workspace_id,
        item["id"],
        list(terminal_statuses),
        option_id,
        expected_item_owner(item, user),
    )
    if not updated or str(updated.get("item_id")) != str(item["id"]):
        raise HTTPException(404, "control room item not found")
    await record_item_event(
        conn,
        user=dict(user),
        item=dict(item),
        event_type="option_selected",
        metadata={"option_id": option_id},
    )
