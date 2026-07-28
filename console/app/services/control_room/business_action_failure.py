from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from app.services.control_room.business_action_reservation import (
    ActionReservation,
    ReservationConflict,
    complete_action_reservation,
)
from app.services.control_room.business_action_attempt import (
    has_remote_attempt,
    mark_remote_attempt_ambiguous,
    remote_attempt_status,
)
from app.services.control_room.business_external_effect import RemoteSideEffectCommitted
from app.services.control_room.business_external_outcome import (
    AMBIGUOUS_OUTCOME_ERROR_CODE,
)
from app.services.control_room.business_external_projection import (
    project_committed_external_effect,
)
from app.services.control_room.business_external_receipt_contract import (
    reservation_stored_authority_audit_valid,
)
from app.services.control_room.business_reservation_lease import (
    reservation_lease_token,
)


def _error_code(error: Exception) -> str:
    detail = getattr(error, "detail", None)
    if isinstance(detail, Mapping):
        code = str(detail.get("code") or "").strip()
        if code:
            return code[:120]
    return "execution_aborted_after_reservation"


def _must_preserve_pending_reservation(error: Exception) -> bool:
    return _error_code(error) == "item_business_state_changed"


def _remote_side_effect(
    error: Exception,
) -> tuple[Mapping[str, Any], Mapping[str, Any], str] | None:
    if isinstance(error, RemoteSideEffectCommitted) or (
        isinstance(getattr(error, "execution_result", None), Mapping)
        and isinstance(getattr(error, "side_effect", None), Mapping)
    ):
        return (
            getattr(error, "execution_result"),
            getattr(error, "side_effect"),
            str(getattr(error, "cause_type", type(error).__name__)),
        )
    return None


async def _lock_current_reservation(
    conn: Any,
    *,
    workspace_id: str,
    reservation: ActionReservation,
) -> dict[str, Any]:
    row = await conn.fetchrow(
        """
        SELECT * FROM action_runs
         WHERE workspace_id = $1::uuid
           AND id = $2
           AND idempotency_key = $3
         FOR UPDATE
        """,
        workspace_id,
        reservation.id,
        reservation.effective_key,
    )
    if not row:
        raise ReservationConflict("action reservation is no longer available")
    current = dict(row)
    expected_token = reservation_lease_token(reservation.row)
    current_token = reservation_lease_token(current)
    if expected_token != current_token and (expected_token or current_token):
        raise ReservationConflict("action reservation lease changed")
    return current


async def finalize_aborted_action_reservation(
    conn: Any,
    *,
    workspace_id: str,
    reservation: ActionReservation,
    error: Exception,
) -> dict[str, Any]:
    if remote := _remote_side_effect(error):
        if not reservation_stored_authority_audit_valid(reservation.row):
            raise ReservationConflict("action reservation authority audit is invalid")
        execution_result, side_effect, cause_type = remote
        result = {
            **execution_result,
            "local_projection_status": "pending_reconciliation",
            "local_projection_error": cause_type,
        }
        pending = await complete_action_reservation(
            conn,
            workspace_id=workspace_id,
            reservation_id=reservation.id,
            effective_key=reservation.effective_key,
            status="completed",
            execution_result=result,
            side_effect=side_effect,
            error_code="local_projection_failed_after_remote_success",
            error_message="remote write completed; local projection requires reconciliation",
        )
        projected = await project_committed_external_effect(
            conn,
            workspace_id=workspace_id,
            reservation_id=reservation.id,
            effective_key=reservation.effective_key,
        )
        return projected or pending
    current = await _lock_current_reservation(
        conn,
        workspace_id=workspace_id,
        reservation=reservation,
    )
    if str(current.get("status") or "") != "pending":
        return current
    if has_remote_attempt(current):
        if remote_attempt_status(current) == "started":
            return await mark_remote_attempt_ambiguous(
                conn,
                workspace_id=workspace_id,
                reservation_id=reservation.id,
                effective_key=reservation.effective_key,
                error_code=AMBIGUOUS_OUTCOME_ERROR_CODE,
            )
        return current
    code = _error_code(error)
    return await complete_action_reservation(
        conn,
        workspace_id=workspace_id,
        reservation_id=reservation.id,
        effective_key=reservation.effective_key,
        status="failed",
        execution_result={"ok": False, "executed": False, "error_code": code},
        error_code=code,
        error_message="execution aborted after durable reservation",
    )


async def run_reserved_external_action(
    *,
    run_scoped: Callable[[Callable[[Any], Awaitable[Any]]], Awaitable[Any]],
    prepare: Callable[[Any], Awaitable[Any]],
    execute: Callable[[Any], Awaitable[dict[str, Any]]],
    finalize: Callable[..., Awaitable[Any]],
    workspace_id: str,
    reservation: ActionReservation,
) -> dict[str, Any]:
    try:
        await run_scoped(prepare)
        return await run_scoped(execute)
    except Exception as error:
        if _must_preserve_pending_reservation(error):
            raise
        await run_scoped(
            lambda conn: finalize(
                conn,
                workspace_id=workspace_id,
                reservation=reservation,
                error=error,
            )
        )
        raise


__all__ = ("finalize_aborted_action_reservation", "run_reserved_external_action")
