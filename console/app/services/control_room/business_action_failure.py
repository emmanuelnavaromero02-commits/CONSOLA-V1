from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from app.services.control_room.business_action_reservation import (
    ActionReservation,
    complete_action_reservation,
)


def _error_code(error: Exception) -> str:
    detail = getattr(error, "detail", None)
    if isinstance(detail, Mapping):
        code = str(detail.get("code") or "").strip()
        if code:
            return code[:120]
    return "execution_aborted_after_reservation"


async def finalize_aborted_action_reservation(
    conn: Any,
    *,
    workspace_id: str,
    reservation: ActionReservation,
    error: Exception,
) -> dict[str, Any]:
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
    execute: Callable[[Any], Awaitable[dict[str, Any]]],
    finalize: Callable[..., Awaitable[Any]],
    workspace_id: str,
    reservation: ActionReservation,
) -> dict[str, Any]:
    try:
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
