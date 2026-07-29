from __future__ import annotations

from collections.abc import Mapping
from typing import Any


async def append_intent_event(
    conn: Any,
    *,
    intent: Mapping[str, Any],
    actor_user_id: int,
    event_type: str,
    from_state: str,
    to_state: str,
    intent_version: int,
    result_code: str,
    operation_digest: str,
) -> int:
    row = await conn.fetchrow(
        """
        INSERT INTO control_room_action_intent_events (
            tenant_id, workspace_id, intent_id, actor_user_id, event_type,
            from_state, to_state, intent_version, result_code,
            correlation_id, operation_digest
        ) VALUES (
            $1::uuid, $2::uuid, $3::uuid, $4, $5,
            $6, $7, $8, $9, $10::uuid, $11
        )
        RETURNING id
        """,
        str(intent["tenant_id"]),
        str(intent["workspace_id"]),
        str(intent["id"]),
        int(actor_user_id),
        event_type,
        from_state,
        to_state,
        int(intent_version),
        result_code,
        str(intent["correlation_id"]),
        operation_digest,
    )
    if not row:
        raise RuntimeError("control room authority ledger write failed")
    return int(row["id"])


__all__ = ("append_intent_event",)
