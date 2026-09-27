from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException

from app.services import audit_service, control_room_service
from app.services.control_room.business_action_authority_policy import (
    EXECUTABLE_TEMPLATE_ID,
)
from app.services.control_room.business_action_mutations import require_exact_count
from app.services.control_room.business_council_actors import (
    CouncilMaker,
    require_council_distinct_actors,
)
from app.services.control_room.business_repository import (
    approve_control_room_decision,
)
from app.services.control_room.business_workflow_quarantine import (
    workflow_is_quarantined,
)
from app.services.control_room.business_workflow_state import (
    NONTERMINAL_EXECUTION_STATUSES,
)


APPROVE_AND_FOLLOW_UP_AUDIT = "control_room.council.approve_and_follow_up"
COUNCIL_ORIGIN = "control_room_council"
FOLLOWUP_ADAPTER = "internal_followup_task"
APPROVAL_ACTION_PREFIX = "Aprobacion en el Consejo de Acciones: "
FOLLOWUP_ACTION_PREFIX = "Seguimiento operativo Control Room: "
MAKER_NOTES = {
    "system": "Propuesta sugerida por el sistema; aprobada por una persona del equipo.",
    "person": "Propuesta creada por una persona del equipo; aprobada por otra.",
}
LOCK_DECISION_SQL = """
SELECT id, status
  FROM decisions
 WHERE id = $1 AND workspace_id = $2
 FOR UPDATE
"""
INSERT_DECISION_ACTION_SQL = """
INSERT INTO decision_actions (decision_id, action_text, note, actor)
VALUES ($1, $2, $3, $4)
RETURNING id
"""
MARK_EXECUTED_SQL = """
UPDATE control_room_items
   SET execution_status = 'executed',
       metadata = COALESCE(metadata, '{}'::jsonb) || $5::jsonb,
       last_seen_at = NOW()
 WHERE workspace_id = $1
   AND item_id = $2
   AND owner_user_id IS NOT DISTINCT FROM $3
   AND decision_id = $4
   AND status = 'approved'
"""


@dataclass(frozen=True)
class FollowupEffect:
    decision_id: int
    approval_action_id: int
    followup_action_id: int


def proposal_changed() -> HTTPException:
    return HTTPException(
        409,
        {
            "code": "proposal_changed",
            "message": "council proposal changed; reload before approving",
        },
    )


def followup_note(item: Mapping[str, Any], row: Mapping[str, Any]) -> str:
    cartridge = item.get("cartridge") or row.get("cartridge_id")
    dataset = item.get("source_dataset") or row.get("source_dataset")
    entity = (
        item.get("entity_label")
        or row.get("entity_label")
        or item.get("entity_id")
        or row.get("entity_id")
    )
    return (
        f"Template {EXECUTABLE_TEMPLATE_ID} ejecutado sobre {cartridge} / "
        f"{dataset}. Target interno: decision_actions. "
        f"Entidad: {entity}. "
        "No se escribio en ERP."
    )


def _require_followup_stage(row: Mapping[str, Any], decision_id: int) -> None:
    status = str(row.get("status") or "").strip().lower()
    execution = str(row.get("execution_status") or "not_started").strip().lower()
    linked = row.get("decision_id")
    if (
        status != "decision_created"
        or linked is None
        or int(linked) != int(decision_id)
        or execution not in NONTERMINAL_EXECUTION_STATUSES
        or workflow_is_quarantined(row)
    ):
        raise proposal_changed()


async def _insert_action(
    conn: Any, decision_id: int, text: str, note: str, actor: str
) -> int:
    action_id = await conn.fetchval(
        INSERT_DECISION_ACTION_SQL, decision_id, text, note, actor
    )
    if action_id is None:
        raise RuntimeError("council decision action was not persisted")
    return int(action_id)


async def complete_followup_effect(
    conn: Any,
    *,
    checker: Mapping[str, Any],
    row: Mapping[str, Any],
    item: Mapping[str, Any],
    decision_id: int,
    maker: CouncilMaker,
    intent_id: str | None = None,
    replay: Mapping[str, str] | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> FollowupEffect:
    checker_id = int(checker["id"])
    require_council_distinct_actors(maker, checker_id)
    _require_followup_stage(row, decision_id)
    workspace_id = str(row["workspace_id"])
    item_id = str(row["item_id"])
    owner_user_id = row.get("owner_user_id")
    decision = await conn.fetchrow(LOCK_DECISION_SQL, decision_id, workspace_id)
    if not decision or str(decision.get("status") or "") != "open":
        raise proposal_changed()
    await approve_control_room_decision(
        conn,
        workspace_id=workspace_id,
        item_id=item_id,
        decision_id=decision_id,
        owner_user_id=owner_user_id,
        item={
            **dict(item),
            "id": item_id,
            "decision_id": decision_id,
            "selected_option_id": row.get("selected_option_id"),
        },
        lessons=(),
    )
    actor = str(checker.get("email") or "user")
    title = str(item.get("title") or row.get("title") or "").strip()
    approval_id = await _insert_action(
        conn,
        decision_id,
        APPROVAL_ACTION_PREFIX + title,
        MAKER_NOTES[maker.origin],
        actor,
    )
    followup_id = await _insert_action(
        conn,
        decision_id,
        FOLLOWUP_ACTION_PREFIX + title,
        followup_note(item, row),
        actor,
    )
    actors = {
        "decision_id": decision_id,
        "maker": maker.label,
        "maker_user_id": maker.user_id,
        "checker_user_id": checker_id,
    }
    writeback = {
        "execution_status": "executed",
        "writeback_result": {
            "adapter": FOLLOWUP_ADAPTER,
            "target": "decision_actions",
            "origin": COUNCIL_ORIGIN,
            "decision_action_id": followup_id,
            **actors,
        },
    }
    result = await conn.execute(
        MARK_EXECUTED_SQL,
        workspace_id,
        item_id,
        owner_user_id,
        decision_id,
        json.dumps(writeback),
    )
    require_exact_count(result, "UPDATE")
    await control_room_service._record_item_event(
        conn,
        user=dict(checker),
        item={"id": item_id},
        event_type="approved",
        metadata={**actors, "action_id": approval_id, "origin": COUNCIL_ORIGIN},
        critical=True,
    )
    await control_room_service._record_item_event(
        conn,
        user=dict(checker),
        item={"id": item_id},
        event_type="action_executed",
        metadata={
            **actors,
            **dict(replay or {}),
            "template_id": EXECUTABLE_TEMPLATE_ID,
            "decision_action_id": followup_id,
            "origin": COUNCIL_ORIGIN,
            "external_write": False,
        },
        critical=True,
    )
    await audit_service.record_event(
        connection=conn,
        user_id=checker_id,
        email=checker.get("email"),
        action=APPROVE_AND_FOLLOW_UP_AUDIT,
        resource_type="control_room_item",
        resource_id=item_id,
        ip=ip,
        user_agent=user_agent,
        status="success",
        metadata={
            **actors,
            "intent_id": intent_id,
            "approval_action_id": approval_id,
            "followup_action_id": followup_id,
            "external_write": False,
        },
        critical=True,
    )
    return FollowupEffect(decision_id, approval_id, followup_id)


__all__ = (
    "APPROVE_AND_FOLLOW_UP_AUDIT",
    "COUNCIL_ORIGIN",
    "FOLLOWUP_ACTION_PREFIX",
    "FollowupEffect",
    "complete_followup_effect",
    "followup_note",
    "proposal_changed",
)
