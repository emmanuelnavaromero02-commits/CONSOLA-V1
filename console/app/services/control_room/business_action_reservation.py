from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from uuid import uuid4

from fastapi import HTTPException

from app.services.control_room.business_access import workspace_scope
from app.services.control_room.business_action_key import effective_action_key
from app.services.control_room.business_action_replay import (
    action_reservation_contract,
    canonical_json,
    json_mapping,
    matching_action_replay,
)
from app.services.control_room.business_action_attempt import REMOTE_ATTEMPT_KEY
from app.services.control_room.business_external_receipt_contract import (
    authority_audit_matches_contract,
)
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
    RESERVATION_LEASE_SECONDS,
    RESERVATION_LEASE_TOKEN_KEY,
    reservation_lease_expired,
    reservation_lease_token,
)


class ReservationState(StrEnum):
    ACQUIRED = "acquired"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class ReservationConflict(RuntimeError):
    pass


AUTHORITY_AUDIT_KEY = "authority_audit"


@dataclass(frozen=True)
class ActionReservation:
    id: int
    effective_key: str
    state: ReservationState
    row: dict[str, Any]

    @property
    def lease_token(self) -> str:
        return reservation_lease_token(self.row)


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


def _reservation_metadata(
    contract: Mapping[str, Any],
    authority_audit: Mapping[str, Any] | None,
    lease_token: str,
) -> dict[str, Any]:
    metadata = {
        "reservation_contract": dict(contract),
        RESERVATION_LEASE_TOKEN_KEY: lease_token,
    }
    if authority_audit is not None:
        metadata[AUTHORITY_AUDIT_KEY] = dict(authority_audit)
    return metadata


def _require_matching_authority_audit(
    row: Mapping[str, Any],
    authority_audit: Mapping[str, Any] | None,
    contract: Mapping[str, Any],
) -> None:
    metadata = json_mapping(row.get("metadata"))
    stored_present = AUTHORITY_AUDIT_KEY in metadata
    incoming_present = authority_audit is not None
    stored = metadata.get(AUTHORITY_AUDIT_KEY)
    if stored_present != incoming_present or (
        incoming_present
        and (
            not isinstance(stored, Mapping)
            or not authority_audit_matches_contract(metadata, contract)
            or canonical_json(stored) != canonical_json(authority_audit)
        )
    ):
        raise ReservationConflict("action reservation authority audit mismatch")


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
    authority_audit: Mapping[str, Any] | None = None,
    authority_refresh_guard: Callable[[], None] | None = None,
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
    if authority_audit is not None and not authority_audit_matches_contract(
        {AUTHORITY_AUDIT_KEY: authority_audit}, contract
    ):
        raise ReservationConflict("action reservation authority audit is invalid")
    lease_token = uuid4().hex
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
        canonical_json(_reservation_metadata(contract, authority_audit, lease_token)),
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
    if _state(existing.get("status")) == ReservationState.IN_PROGRESS and (
        reservation_lease_expired(existing)
    ):
        if REMOTE_ATTEMPT_KEY in metadata:
            _require_matching_authority_audit(existing, authority_audit, contract)
            return _reservation(existing, key, acquired=False)
        stored_audit = metadata.get(AUTHORITY_AUDIT_KEY)
        stored_present = AUTHORITY_AUDIT_KEY in metadata
        incoming_present = authority_audit is not None
        audit_changed = stored_present != incoming_present or (
            incoming_present
            and canonical_json(stored_audit) != canonical_json(authority_audit)
        )
        if audit_changed:
            if (
                authority_refresh_guard is None
                or not isinstance(stored_audit, Mapping)
                or authority_audit is None
                or not authority_audit_matches_contract(metadata, contract)
            ):
                raise ReservationConflict("action reservation authority audit mismatch")
            authority_refresh_guard()
        reclaimed = await reservation_fetchrow(
            conn,
            """
            UPDATE action_runs
               SET updated_at = NOW(),
                   metadata = (CASE
                       WHEN $7::jsonb IS NULL THEN COALESCE(metadata, '{}'::jsonb)
                       ELSE jsonb_set(
                           COALESCE(metadata, '{}'::jsonb),
                           '{authority_audit}',
                           $7::jsonb,
                           true
                       )
                   END) || jsonb_build_object(
                       'reservation_lease_token', $8::text
                   )
             WHERE workspace_id = $1::uuid
               AND id = $2
               AND idempotency_key = $3
               AND status = 'pending'
               AND updated_at = $4
               AND updated_at <= NOW() - ($5 * INTERVAL '1 second')
               AND (metadata -> 'authority_audit')
                   IS NOT DISTINCT FROM $6::jsonb
               AND NOT (COALESCE(metadata, '{}'::jsonb) ? 'remote_attempt')
               AND (metadata ->> 'reservation_lease_token')
                   IS NOT DISTINCT FROM $9::text
               AND metadata -> 'reservation_contract' = $10::jsonb
             RETURNING *
            """,
            workspace_id,
            int(existing["id"]),
            key,
            existing["updated_at"],
            RESERVATION_LEASE_SECONDS,
            (
                canonical_json(dict(stored_audit))
                if isinstance(stored_audit, Mapping)
                else None
            ),
            (
                canonical_json(dict(authority_audit))
                if authority_audit is not None
                else None
            ),
            lease_token,
            reservation_lease_token(existing) or None,
            canonical_json(dict(contract)),
        )
        if reclaimed:
            return _reservation(reclaimed, key, acquired=True)
        current = await reservation_fetchrow(
            conn,
            """
            SELECT * FROM action_runs
             WHERE workspace_id = $1::uuid AND idempotency_key = $2
            """,
            workspace_id,
            key,
        )
        if not current:
            raise ReservationConflict("action reservation is no longer available")
        current_metadata = json_mapping(current.get("metadata"))
        current_contract = current_metadata.get("reservation_contract")
        if not isinstance(current_contract, Mapping) or canonical_json(
            current_contract
        ) != canonical_json(contract):
            raise ReservationConflict("action reservation contract mismatch")
        _require_matching_authority_audit(current, authority_audit, contract)
        return _reservation(current, key, acquired=False)
    _require_matching_authority_audit(existing, authority_audit, contract)
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
    authority_audit: Mapping[str, Any] | None = None,
    reservation_guard: Callable[[], None] | None = None,
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
        contract = action_reservation_contract(
            workspace_id=workspace_id,
            item=item,
            template_id=template_id,
            operation=operation,
            authorization_contract=authorization,
            input_payload=input_payload,
        )
        _require_matching_authority_audit(row, authority_audit, contract)
        return _reservation(row, key, acquired=False)
    await require_no_legacy_action_reservation(
        conn,
        workspace_id=workspace_id,
        item=item,
        template_id=template_id,
        operation=operation,
    )
    if reservation_guard is not None:
        reservation_guard()
    reservation = await acquire_action_reservation(
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
        authority_audit=authority_audit,
        authority_refresh_guard=reservation_guard,
    )
    if reservation_guard is not None and reservation.state == ReservationState.ACQUIRED:
        reservation_guard()
    return reservation


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
    "AUTHORITY_AUDIT_KEY",
    "ActionReservation",
    "ReservationConflict",
    "ReservationState",
    "RESERVATION_LEASE_TOKEN_KEY",
    "acquire_action_reservation",
    "acquire_guarded_action_reservation",
    "complete_action_reservation",
    "effective_action_key",
    "reservation_lease_token",
)
