from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any

from fastapi import HTTPException

import app.services.control_room.business_execution_provenance_sql as _exec_sql
from app.services.control_room.business_action_reservation import (
    ActionReservation,
    ReservationState,
)
from app.services.control_room.business_action_attempt import has_remote_attempt
from app.services.control_room.business_external_receipt_contract import (
    receipt_contract_matches,
    reservation_stored_authority_audit_valid,
)


_RECEIPT_SQL = """
SELECT *
  FROM action_runs
 WHERE workspace_id = $1::uuid
   AND id = $2
   AND idempotency_key = $3
   AND status = 'completed'
 FOR UPDATE
"""

_ITEM_SQL = """
SELECT tenant_id::text AS tenant_id,
       workspace_id::text AS workspace_id,
       owner_user_id, item_id, cartridge_id, domain, source_dataset,
       item_kind, title, severity, status, decision_id, entity_kind,
       entity_id, entity_label, anomaly_type, metadata,
       impact_estimate, impact_currency, confidence, priority_score,
       selected_option_id, execution_status, first_seen_at, last_seen_at,
       resolved_at, dismissed_at
  FROM control_room_items
 WHERE workspace_id = $1::uuid
   AND item_id = $2
 FOR UPDATE
"""


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return dict(parsed) if isinstance(parsed, Mapping) else {}
    return {}


async def project_committed_external_effect(
    conn: Any,
    *,
    workspace_id: str,
    reservation_id: int,
    effective_key: str,
) -> dict[str, Any] | None:
    receipt_value = await conn.fetchrow(
        _RECEIPT_SQL,
        workspace_id,
        int(reservation_id),
        effective_key,
    )
    if not receipt_value:
        return None
    receipt = dict(receipt_value)
    item_value = await conn.fetchrow(
        _ITEM_SQL,
        workspace_id,
        str(receipt.get("item_id") or ""),
    )
    if not item_value:
        return None
    item_row = dict(item_value)
    if not receipt_contract_matches(receipt, item_row, workspace_id):
        return None
    result = _mapping(receipt.get("execution_result"))
    if result.get("local_projection_status") == "completed":
        return receipt if item_row.get("execution_status") == "executed" else None
    projected = await conn.fetchrow(
        f"""
        UPDATE control_room_items
           SET execution_status = 'executed',
               metadata = {_exec_sql.EXECUTED_METADATA_STATUS_SQL},
               last_seen_at = NOW()
         WHERE workspace_id = $1::uuid
           AND item_id = $2
           AND decision_id = $3
           AND status = 'approved'
           AND COALESCE(execution_status, 'not_started')
               IN ('dry_run_validated', 'executed')
         RETURNING item_id
        """,
        workspace_id,
        str(item_row["item_id"]),
        int(item_row["decision_id"]),
    )
    if not projected:
        return None
    completed_result = {**result, "local_projection_status": "completed"}
    updated = await conn.fetchrow(
        """
        UPDATE action_runs
           SET execution_result = $4::jsonb,
               error_code = NULL,
               error_message = NULL,
               updated_at = NOW()
         WHERE workspace_id = $1::uuid
           AND id = $2
           AND idempotency_key = $3
           AND status = 'completed'
           AND execution_result ->> 'local_projection_status'
               = 'pending_reconciliation'
         RETURNING *
        """,
        workspace_id,
        int(reservation_id),
        effective_key,
        json.dumps(completed_result, sort_keys=True, separators=(",", ":")),
    )
    if not updated:
        raise RuntimeError("external receipt projection changed concurrently")
    return dict(updated)


def reserved_action_response(
    reservation: ActionReservation,
    *,
    item: dict[str, Any],
    payload: dict[str, Any],
    action_run_public: Callable[..., dict[str, Any]],
    details: Callable[[Any], dict[str, Any]],
    project_item: Callable[..., dict[str, Any]],
    omega_builder: Callable[..., Any],
) -> dict[str, Any]:
    if reservation.state == ReservationState.IN_PROGRESS:
        code = (
            "external_action_pending_reconciliation"
            if has_remote_attempt(reservation.row)
            else "action_in_progress"
        )
        raise HTTPException(
            409,
            {
                "code": code,
                "reservation_id": reservation.id,
                "idempotency_key": reservation.effective_key,
            },
        )
    if reservation.state == ReservationState.FAILED:
        raise HTTPException(
            409,
            {
                "code": "action_previously_failed",
                "reservation_id": reservation.id,
                "idempotency_key": reservation.effective_key,
            },
        )
    if reservation.state != ReservationState.COMPLETED:
        raise RuntimeError("acquired action reservation cannot be replayed")
    if not reservation_stored_authority_audit_valid(reservation.row):
        raise HTTPException(
            409,
            {
                "code": "invalid_execution_authority_audit",
                "reservation_id": reservation.id,
            },
        )
    action_run = action_run_public(reservation.row)
    result = details(action_run.get("execution_result"))
    if result.get("executed") is not True:
        raise HTTPException(
            409,
            {
                "code": "invalid_execution_receipt",
                "reservation_id": reservation.id,
            },
        )
    is_external_write = result.get("external_write") is True
    executed = True
    projection_status = str(result.get("local_projection_status") or "")
    execution_committed = executed and (
        not is_external_write
        or projection_status in {"", "completed"}
        or str(item.get("execution_status") or "") == "executed"
    )
    if result.get("local_projection_status") == "pending_reconciliation" or (
        is_external_write and executed and not execution_committed
    ):
        raise HTTPException(
            409,
            {
                "code": "external_action_pending_reconciliation",
                "reservation_id": reservation.id,
                "idempotency_key": reservation.effective_key,
            },
        )
    return {
        "executed": executed,
        "idempotent": True,
        "execution": {},
        "action_run": action_run,
        "payload": details(action_run.get("input")) or payload,
        "result": {**result, "idempotent": True},
        "item": project_item(
            item,
            omega_builder,
            execution_status=(
                "executed"
                if execution_committed
                else str(item.get("execution_status") or "not_started")
            ),
        ),
    }


__all__ = ("project_committed_external_effect", "reserved_action_response")
