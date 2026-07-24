from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from app.services.control_room.business_action_reservation import (
    ActionReservation,
    complete_action_reservation,
)
from app.services.control_room.business_external_effect import RemoteSideEffectCommitted
from app.services.control_room.business_external_projection import (
    project_committed_external_effect,
)


def _error_code(error: Exception) -> str:
    detail = getattr(error, "detail", None)
    if isinstance(detail, Mapping):
        code = str(detail.get("code") or "").strip()
        if code:
            return code[:120]
    return "execution_aborted_after_reservation"


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


async def finalize_aborted_action_reservation(
    conn: Any,
    *,
    workspace_id: str,
    reservation: ActionReservation,
    error: Exception,
) -> dict[str, Any]:
    if remote := _remote_side_effect(error):
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
