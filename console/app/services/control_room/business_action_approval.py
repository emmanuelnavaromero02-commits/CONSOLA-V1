from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException

from app.services.control_room.business_access import (
    actor_id,
    can_read_workspace_wide,
    expected_item_owner,
)
from app.services.control_room.business_action_mutations import (
    command_count,
    _record_event,
)
from app.services.control_room.business_mutation_guard import (
    lock_authoritative_business_item,
)
from app.services.control_room.business_workflow_provenance import (
    WORKFLOW_QUARANTINE_KEY,
    WorkflowStage,
    workflow_has_eligible_provenance,
)


ItemWriter = Callable[..., Awaitable[None]]
DecisionLinker = Callable[..., Awaitable[None]]


@dataclass(frozen=True)
class ApprovableDecision:
    row: Any
    linked: bool


async def require_approvable_decision(
    conn: Any,
    *,
    user: Mapping[str, Any],
    item: Mapping[str, Any],
    workspace_id: str,
    decision_id: int,
) -> ApprovableDecision:
    decision = await conn.fetchrow(
        """
        SELECT id, created_by_id
          FROM decisions
         WHERE id = $1 AND workspace_id = $2
        """,
        decision_id,
        workspace_id,
    )
    if not decision:
        raise HTTPException(404, "decision not found")
    item_row = await conn.fetchrow(
        """
        SELECT item_id, decision_id, selected_option_id, owner_user_id,
               item_kind, metadata
          FROM control_room_items
         WHERE workspace_id = $1 AND item_id = $2
         FOR UPDATE
        """,
        workspace_id,
        item["id"],
    )
    owner = actor_id(item_row.get("owner_user_id")) if item_row else None
    actor = actor_id(user.get("id"))
    workspace_wide = can_read_workspace_wide(user)
    if item_row and not workspace_wide and owner != actor:
        raise HTTPException(404, "decision not found")
    own_link = bool(item_row and item_row.get("decision_id") == decision_id)
    creator = actor_id(decision.get("created_by_id"))
    if not workspace_wide and not own_link and creator != actor:
        raise HTTPException(404, "decision not found")
    conflicting = await conn.fetchrow(
        """
        SELECT item_id, owner_user_id
          FROM control_room_items
         WHERE workspace_id = $1 AND decision_id = $2 AND item_id <> $3
         LIMIT 1
        """,
        workspace_id,
        decision_id,
        item["id"],
    )
    if conflicting:
        other_owner = actor_id(conflicting.get("owner_user_id"))
        if not workspace_wide and other_owner != actor:
            raise HTTPException(404, "decision not found")
        raise HTTPException(409, "decision is linked to another control room item")
    existing_link = item_row.get("decision_id") if item_row else None
    if existing_link is not None and int(existing_link) != decision_id:
        raise HTTPException(409, "control room item has another decision")
    metadata = item_row.get("metadata") if item_row else {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except (TypeError, ValueError, json.JSONDecodeError):
            metadata = {}
    if isinstance(metadata, Mapping) and metadata.get(WORKFLOW_QUARANTINE_KEY):
        raise HTTPException(409, "control room item has quarantined workflow")
    persisted_option = str(
        (item_row.get("selected_option_id") if item_row else None)
        or (metadata.get("selected_option_id") if isinstance(metadata, Mapping) else None)
        or ""
    ).strip()
    expected_option = str(item.get("selected_option_id") or "").strip()
    if persisted_option != expected_option:
        raise HTTPException(409, "control room option changed before approval")
    eligible_provenance = workflow_has_eligible_provenance(
        metadata if isinstance(metadata, Mapping) else {},
        {**dict(item), "workspace_id": workspace_id},
        decision_id=decision_id,
        use_stored_fingerprint=True,
        allowed_stages=(WorkflowStage.DECISION_CREATED,),
    )
    if not eligible_provenance:
        raise HTTPException(409, "decision is not linked to this control room item")
    return ApprovableDecision(row=decision, linked=own_link)


async def _persist_lessons(
    conn: Any,
    *,
    user: Mapping[str, Any],
    item: Mapping[str, Any],
    decision_id: int,
    lessons: Sequence[str],
    confidence: float,
) -> None:
    for rule in lessons:
        result = await conn.execute(
            """
            INSERT INTO control_room_lessons (
                tenant_id, workspace_id, item_id, cartridge_id, anomaly_type,
                rule, source_decision_id, confidence, metadata
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb)
            """,
            user.get("active_tenant_id") or user.get("tenant_id"),
            user.get("active_workspace_id") or user.get("workspace_id"),
            item["id"],
            item.get("cartridge") or "platform",
            item.get("anomaly_type") or "control_room_item",
            rule,
            decision_id,
            confidence,
            json.dumps(
                {
                    "source_dataset": item.get("source_dataset"),
                    "status": item.get("status"),
                }
            ),
        )
        if command_count(result, "INSERT") != 1:
            raise RuntimeError("control room lesson was not persisted")


async def approve_business_item(
    conn: Any,
    *,
    user: Mapping[str, Any],
    item: Mapping[str, Any],
    workspace_id: str,
    decision_id: int,
    lessons: Sequence[str],
    confidence: float,
    ensure_item_row: ItemWriter,
    link_decision: DecisionLinker,
    approve_link: DecisionLinker,
) -> Any:
    approved = await require_approvable_decision(
        conn,
        user=user,
        item=item,
        workspace_id=workspace_id,
        decision_id=decision_id,
    )
    await lock_authoritative_business_item(
        conn,
        user=user,
        item=item,
        decision_id=decision_id,
        allowed_stages=(WorkflowStage.DECISION_CREATED,),
    )
    if not approved.linked:
        await link_decision(
            conn,
            workspace_id=workspace_id,
            item_id=item["id"],
            decision_id=decision_id,
            owner_user_id=expected_item_owner(item, user),
            item=item,
        )
    action = await conn.fetchrow(
        """
        INSERT INTO decision_actions (decision_id, action_text, note, actor)
        VALUES ($1, $2, $3, $4)
        RETURNING *
        """,
        decision_id,
        f"Aprobacion de recomendacion OMEGA: {item['title']}",
        item["recommendation"],
        user.get("email") or "user",
    )
    if not action or action.get("id") is None:
        raise RuntimeError("control room approval action was not persisted")
    await approve_link(
        conn,
        workspace_id=workspace_id,
        item_id=item["id"],
        decision_id=decision_id,
        owner_user_id=expected_item_owner(item, user),
        item=item,
        lessons=list(lessons),
    )
    await _record_event(
        conn,
        user=user,
        item=item,
        event_type="approved",
        metadata={"decision_id": decision_id, "action_id": action["id"]},
    )
    await _record_event(
        conn,
        user=user,
        item=item,
        event_type="lesson_recorded",
        metadata={"decision_id": decision_id, "lessons": list(lessons)},
    )
    await _persist_lessons(
        conn,
        user=user,
        item=item,
        decision_id=decision_id,
        lessons=lessons,
        confidence=confidence,
    )
    return action


__all__ = (
    "ApprovableDecision",
    "approve_business_item",
    "require_approvable_decision",
)
