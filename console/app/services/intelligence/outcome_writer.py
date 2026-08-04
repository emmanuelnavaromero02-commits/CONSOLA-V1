from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from fastapi import HTTPException

from app.services.intelligence.utils import json_dumps


@dataclass(frozen=True)
class OutcomeWrite:
    row: Any
    inserted: bool


def is_authoritatively_evaluated_outcome(row: Any) -> bool:
    metadata = row.get("metadata") if hasattr(row, "get") else None
    return bool(
        isinstance(metadata, dict)
        and metadata.get("input_classification") == "observed"
        and row.get("evaluation_status") in {"hit", "miss"}
        and row.get("evaluated_by") == "omega_outcome_evaluator.v1"
        and row.get("evaluated_at")
    )


async def record_server_owned_outcome(
    conn: Any,
    *,
    tenant_id: str | None,
    workspace_id: str,
    signal_id: str,
    option_id: str | None,
    action_taken: str,
    actual_value: float | None,
    outcome_summary: str,
    learned_rule: str | None,
    owner_user_id: int | None,
    metadata: dict[str, Any],
) -> OutcomeWrite:
    try:
        row = await conn.fetchrow(
            """
            SELECT *
              FROM record_prediction_outcome(
                  $1::uuid, $2::uuid, $3::text, $4::text, $5::text,
                  $6::numeric, $7::text, $8::text,
                  $9::bigint, $10::jsonb
              )
            """,
            tenant_id,
            workspace_id,
            signal_id,
            option_id,
            action_taken,
            actual_value,
            outcome_summary,
            learned_rule,
            owner_user_id,
            json_dumps(metadata),
        )
    except Exception as exc:
        sqlstate = str(getattr(exc, "sqlstate", "") or "")
        if sqlstate == "23505":
            raise HTTPException(409, "outcome identity conflict") from exc
        if sqlstate == "42501":
            raise HTTPException(403, "outcome scope denied") from exc
        if sqlstate in {"23503", "23514"}:
            raise HTTPException(409, "outcome evidence unavailable") from exc
        raise
    if not row:
        raise HTTPException(409, "outcome persistence unavailable")
    outcome = row.get("outcome")
    if isinstance(outcome, str):
        outcome = json.loads(outcome)
    if not isinstance(outcome, dict):
        raise HTTPException(409, "outcome persistence unavailable")
    for field in ("created_at", "evaluated_at"):
        value = outcome.get(field)
        if isinstance(value, str):
            try:
                outcome[field] = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                raise HTTPException(409, "outcome persistence unavailable") from None
    return OutcomeWrite(
        row=outcome,
        inserted=bool(row.get("inserted")),
    )


__all__ = (
    "OutcomeWrite",
    "is_authoritatively_evaluated_outcome",
    "record_server_owned_outcome",
)
