from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from fastapi import HTTPException

from app.services.control_room.business_access import workspace_scope
from app.services.control_room.business_action_key import effective_action_key
from app.services.control_room.business_action_replay import (
    action_reservation_contract,
    canonical_json,
    json_mapping,
    matching_action_replay,
)
from app.services.control_room.business_action_attempt import has_remote_attempt
from app.services.control_room.business_reservation_errors import reservation_fetchrow
from app.services.control_room.business_execution_approval import (
    require_approved_execution,
)
from app.services.control_room.business_execution_precondition import (
    execution_authorization_contract,
)
from app.services.control_room.business_legacy_reservation_guard import (
    require_no_legacy_action_reservation,
)
from app.services.control_room.business_reservation_lease import (
    reservation_lease_expired,
)


class ReservationState(StrEnum):
    ACQUIRED = "acquired"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class ReservationConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class ActionReservation:
    id: int
    effective_key: str
    state: ReservationState
    row: dict[str, Any]


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
        input_payload=input_payload,
    )
    contract = action_reservation_contract(
        workspace_id=workspace_id,
        item=item,
        template_id=template_id,
        operation=operation,
        authorization_contract=authorization_contract,
        input_payload=input_payload,
    )
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
        canonical_json(dict(input_payload or {})),
        canonical_json({"reservation_contract": contract}),
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
    metadata = json_mapping(existing.get("metadata"))
    stored = metadata.get("reservation_contract")
    if not isinstance(stored, Mapping) or canonical_json(stored) != canonical_json(
        contract
    ):
        raise ReservationConflict("action reservation contract mismatch")
    if _state(
        existing.get("status")
    ) is ReservationState.IN_PROGRESS and reservation_lease_expired(existing):
        if has_remote_attempt(existing):
            return _reservation(existing, key, acquired=False)
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
    persist_item: Callable[..., Awaitable[Any]] | None = None,
) -> ActionReservation:
    tenant_id, workspace_id = workspace_scope(user)
    if persist_item is not None:
        await persist_item(
            conn,
            user=dict(user),
            item=dict(item),
            status=item.get("status") or "in_review",
            critical=True,
        )
    try:
        await require_approved_execution(
            conn,
            user=user,
            item=item,
            template_id=template_id,
        )
    except HTTPException as exc:
        if exc.status_code != 409:
            raise
        authorization = execution_authorization_contract(user)
        replay = await matching_action_replay(
            conn,
            workspace_id=workspace_id,
            item=item,
            template_id=template_id,
            operation=operation,
            authorization_contract=authorization,
            input_payload=input_payload,
        )
        if replay is None:
            raise
        key, row = replay
        return _reservation(row, key, acquired=False)
    await require_no_legacy_action_reservation(
        conn,
        workspace_id=workspace_id,
        item=item,
        template_id=template_id,
        operation=operation,
    )
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
        canonical_json(dict(execution_result)),
        canonical_json(dict(side_effect or {})),
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
