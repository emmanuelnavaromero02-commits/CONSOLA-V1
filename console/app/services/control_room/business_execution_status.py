from __future__ import annotations

import json
from typing import Any

from fastapi import HTTPException

from app.services.control_room.business_item_persistence import parse_command_tag
from app.services.control_room.business_workflow_state import (
    TERMINAL_EXECUTION_STATUSES,
)


def _status_conflict() -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "code": "execution_status_conflict",
            "message": "control room execution status is already terminal",
        },
    )


async def persist_execution_status(
    conn: Any,
    *,
    workspace_id: str,
    item_id: str,
    owner_user_id: int | None,
    execution_status: str,
) -> None:
    result = await conn.execute(
        """
        UPDATE control_room_items
           SET execution_status = $1,
               metadata = CASE
                   WHEN $1 = ANY($6::text[]) THEN
                       jsonb_set(
                           jsonb_set(
                               COALESCE(metadata, '{}'::jsonb) || $4::jsonb,
                               '{decision_eligibility_provenance,stage}',
                               '"executed"'::jsonb,
                               false
                           ),
                           '{decision_eligibility_provenance,reason}',
                           '"explicit_execution"'::jsonb,
                           false
                       )
                   ELSE COALESCE(metadata, '{}'::jsonb) || $4::jsonb
               END,
               last_seen_at = NOW()
         WHERE workspace_id = $2
           AND item_id = $3
           AND owner_user_id IS NOT DISTINCT FROM $5
           AND (
               COALESCE(execution_status, 'not_started') <> ALL($6::text[])
               OR execution_status = $1
           )
        """,
        execution_status,
        workspace_id,
        item_id,
        json.dumps({"execution_status": execution_status}),
        owner_user_id,
        sorted(TERMINAL_EXECUTION_STATUSES),
    )
    affected = parse_command_tag(result, "UPDATE")
    if affected == 0:
        raise _status_conflict()
    if affected != 1:
        raise RuntimeError("control room update affected unexpected rows")


__all__ = ("TERMINAL_EXECUTION_STATUSES", "persist_execution_status")
