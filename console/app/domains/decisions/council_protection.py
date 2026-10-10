from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from app.services.control_room.business_action_markers import (
    CONTROL_ROOM_ACTION_MARKERS,
    RECOMMENDATION_APPROVAL_PREFIX,
)

COUNCIL_DELETE_PROTECTION = "decisions_council_delete_protection"
COUNCIL_ACTION_PATTERNS = (
    "Aprobacion en el Consejo de Acciones: %",
    "Aprobación en el Consejo de Acciones: %",
    "Seguimiento operativo Control Room: %",
    "Propuesta descartada en el Consejo: %",
)
RESERVED_ACTION_PREFIXES = (
    *(pattern.removesuffix("%") for pattern in COUNCIL_ACTION_PATTERNS),
    RECOMMENDATION_APPROVAL_PREFIX,
)
RESERVED_ACTION_MESSAGE = (
    "Ese texto está reservado para los registros del sistema; "
    "escribe la nota con otras palabras."
)
PROTECTED_DECISION_MESSAGE = (
    "Esta decisión está ligada al Control Room o al Consejo de Acciones; "
    "ciérrala en lugar de eliminarla."
)
PROTECTED_DECISIONS_SQL = """
SELECT decision.id
  FROM decisions AS decision
 WHERE decision.workspace_id = $1
   AND decision.id = ANY($2::bigint[])
   AND (
       EXISTS (
           SELECT 1
             FROM control_room_items AS item
            WHERE item.workspace_id = decision.workspace_id
              AND item.decision_id = decision.id
       )
       OR EXISTS (
           SELECT 1
             FROM decision_actions AS action
            WHERE action.decision_id = decision.id
              AND action.action_text LIKE ANY($3::text[])
       )
   )
"""


async def protected_decision_ids(
    conn: Any, *, workspace_id: Any, decision_ids: Sequence[int]
) -> set[int]:
    ids = sorted({int(value) for value in decision_ids})
    if not ids:
        return set()
    rows = await conn.fetch(
        PROTECTED_DECISIONS_SQL, workspace_id, ids, list(COUNCIL_ACTION_PATTERNS)
    )
    return {int(row["id"]) for row in rows}


def is_reserved_action_text(text: str) -> bool:
    value = text.strip()
    return value in CONTROL_ROOM_ACTION_MARKERS or value.startswith(
        RESERVED_ACTION_PREFIXES
    )


__all__ = (
    "COUNCIL_ACTION_PATTERNS",
    "COUNCIL_DELETE_PROTECTION",
    "PROTECTED_DECISIONS_SQL",
    "PROTECTED_DECISION_MESSAGE",
    "RESERVED_ACTION_MESSAGE",
    "RESERVED_ACTION_PREFIXES",
    "is_reserved_action_text",
    "protected_decision_ids",
)
