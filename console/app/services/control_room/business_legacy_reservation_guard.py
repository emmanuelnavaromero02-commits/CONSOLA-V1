from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException

from app.services.control_room.business_action_key import (
    legacy_effective_action_key_v1,
)
from app.services.control_room.business_reservation_errors import reservation_fetchrow


async def require_no_legacy_action_reservation(
    conn: Any,
    *,
    workspace_id: str,
    item: Mapping[str, Any],
    template_id: str,
    operation: str,
) -> None:

    legacy_key = legacy_effective_action_key_v1(
        workspace_id=workspace_id,
        item=item,
        template_id=template_id,
        operation=operation,
    )
    row = await reservation_fetchrow(
        conn,
        """
        SELECT id
          FROM action_runs
         WHERE workspace_id = $1::uuid
           AND idempotency_key = $2
         LIMIT 1
        """,
        workspace_id,
        legacy_key,
    )
    if row:
        raise HTTPException(
            409,
            {
                "code": "legacy_action_reservation_requires_reconciliation",
                "message": (
                    "A legacy action reservation requires operator reconciliation "
                    "before a new execution can start."
                ),
            },
        )


__all__ = ("require_no_legacy_action_reservation",)
