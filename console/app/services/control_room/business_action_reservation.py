from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from app.services.control_room.business_access import workspace_scope
from app.services.control_room.business_action_key import effective_action_key
from app.services.control_room.business_reservation_errors import reservation_fetchrow
from app.services.control_room.business_execution_approval import (
    require_approved_execution,
)
from app.services.control_room.business_execution_precondition import (
    execution_authorization_contract,
)
from app.services.control_room.business_workflow_provenance import (
    ELIGIBILITY_POLICY_VERSION,
    business_observation_fingerprint,
)


class ReservationState(StrEnum):
    ACQUIRED = "acquired"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class ReservationConflict(RuntimeError):
    pass


RESERVATION_LEASE_SECONDS = 300


@dataclass(frozen=True)
class ActionReservation:
    id: int
    effective_key: str
    state: ReservationState
    row: dict[str, Any]


def _canonical(value: Any) -> str:
    return json.dumps(value, default=str, separators=(",", ":"), sort_keys=True)


def _json(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return dict(parsed) if isinstance(parsed, Mapping) else {}
    return {}


def _state(status: Any) -> ReservationState:
    value = str(status or "").strip()
    if value == "pending":
        return ReservationState.IN_PROGRESS
    if value == "completed":
        return ReservationState.COMPLETED
    if value in {"failed", "blocked"}:
        return ReservationState.FAILED
    raise ReservationConflict("action reservation has invalid state")


def _reservation(
    row: Mapping[str, Any], key: str, *, acquired: bool
) -> ActionReservation:
    data = dict(row)
    return ActionReservation(
        id=int(data["id"]),
        effective_key=key,
        state=ReservationState.ACQUIRED if acquired else _state(data.get("status")),
        row=data,
    )


def _lease_expired(row: Mapping[str, Any]) -> bool:
    updated_at = row.get("updated_at")
    if not isinstance(updated_at, datetime):
        return False
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=UTC)
    return updated_at <= datetime.now(UTC) - timedelta(
        seconds=RESERVATION_LEASE_SECONDS
    )


async def acquire_action_reservation(
    conn: Any,
    *,
    tenant_id: str | None,
    workspace_id: str,
    item: Mapping[str, Any],
    template_id: str,
    adapter_name: str,
    operation: str,
    provided_key: str | None = None,
    input_payload: Mapping[str, Any] | None = None,
    actor_id: int | None = None,
    actor_email: str | None = None,
    authorization_contract: Mapping[str, Any] | None = None,
) -> ActionReservation:
    key = effective_action_key(
        workspace_id=workspace_id,
        item=item,
        template_id=template_id,
        operation=operation,
        provided=provided_key,
    )
    contract = {
        "version": 1,
        "policy_version": ELIGIBILITY_POLICY_VERSION,
        "workspace_id": workspace_id,
        "item_id": str(item.get("id") or item.get("item_id") or ""),
        "fingerprint": business_observation_fingerprint(item),
        "decision_id": item.get("decision_id"),
        "template_id": template_id,
        "operation": operation,
        "authorization": dict(authorization_contract or {}),
    }
    row = await reservation_fetchrow(
        conn,
        """
        INSERT INTO action_runs (
            tenant_id, workspace_id, item_id, decision_id, action_type,
            adapter_name, mode, status, idempotency_key, actor_id,
            actor_email, input, metadata
        )
        VALUES ($1::uuid, $2::uuid, $3, $4, $5,
                $6, 'execute', 'pending', $7, $8,
                $9, $10::jsonb, $11::jsonb)
        ON CONFLICT (workspace_id, idempotency_key) DO NOTHING
        RETURNING *
        """,
        tenant_id,
        workspace_id,
        contract["item_id"],
        item.get("decision_id"),
        template_id,
        adapter_name,
        key,
        actor_id,
        actor_email,
        _canonical(dict(input_payload or {})),
        _canonical({"reservation_contract": contract}),
    )
    if row:
        return _reservation(row, key, acquired=True)
    existing = await reservation_fetchrow(
        conn,
        """
        SELECT * FROM action_runs
         WHERE workspace_id = $1::uuid AND idempotency_key = $2
        """,
        workspace_id,
        key,
    )
    if not existing:
        raise ReservationConflict("action reservation conflict was not observable")
    metadata = _json(existing.get("metadata"))
    stored = metadata.get("reservation_contract")
    if isinstance(stored, Mapping) and _canonical(stored) != _canonical(contract):
        raise ReservationConflict("action reservation contract mismatch")
    if _state(
        existing.get("status")
    ) is ReservationState.IN_PROGRESS and _lease_expired(existing):
        reclaimed = await reservation_fetchrow(
            conn,
            """
            UPDATE action_runs
               SET updated_at = NOW()
             WHERE workspace_id = $1::uuid
               AND id = $2
               AND idempotency_key = $3
               AND status = 'pending'
               AND updated_at = $4
             RETURNING *
            """,
            workspace_id,
            int(existing["id"]),
            key,
            existing["updated_at"],
        )
        if reclaimed:
            return _reservation(reclaimed, key, acquired=True)
    return _reservation(existing, key, acquired=False)


async def acquire_guarded_action_reservation(
    conn: Any,
    *,
    user: Mapping[str, Any],
    item: Mapping[str, Any],
    template_id: str,
    adapter_name: str,
    operation: str,
    provided_key: str | None = None,
    input_payload: Mapping[str, Any] | None = None,
) -> ActionReservation:
    await require_approved_execution(
        conn,
        user=user,
        item=item,
        template_id=template_id,
    )
    tenant_id, workspace_id = workspace_scope(user)
    return await acquire_action_reservation(
        conn,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        item=item,
        template_id=template_id,
        adapter_name=adapter_name,
        operation=operation,
        provided_key=provided_key,
        input_payload=input_payload,
        actor_id=user.get("id"),
        actor_email=str(user.get("email") or "") or None,
        authorization_contract=execution_authorization_contract(user),
    )


async def complete_action_reservation(
    conn: Any,
    *,
    workspace_id: str,
    reservation_id: int,
    effective_key: str,
    status: str,
    execution_result: Mapping[str, Any],
    side_effect: Mapping[str, Any] | None = None,
    legacy_execution_id: int | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> dict[str, Any]:
    if status not in {"completed", "failed"}:
        raise ValueError("reservation completion status is invalid")
    row = await conn.fetchrow(
        """
        UPDATE action_runs
           SET status = $4,
               execution_result = $5::jsonb,
               side_effect = $6::jsonb,
               legacy_execution_id = COALESCE($7, legacy_execution_id),
               error_code = $8,
               error_message = $9,
               updated_at = NOW(),
               completed_at = NOW()
         WHERE workspace_id = $1::uuid
           AND id = $2
           AND idempotency_key = $3
           AND status = 'pending'
         RETURNING *
        """,
        workspace_id,
        int(reservation_id),
        effective_key,
        status,
        _canonical(dict(execution_result)),
        _canonical(dict(side_effect or {})),
        legacy_execution_id,
        error_code,
        error_message,
    )
    if not row:
        raise ReservationConflict("action reservation state changed")
    return dict(row)


__all__ = (
    "ActionReservation",
    "ReservationConflict",
    "ReservationState",
    "acquire_action_reservation",
    "acquire_guarded_action_reservation",
    "complete_action_reservation",
    "effective_action_key",
)
