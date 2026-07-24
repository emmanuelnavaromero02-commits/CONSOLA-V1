from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from fastapi import HTTPException

from app.services.control_room.business_access import workspace_scope
from app.services.control_room.business_action_replay import matching_action_replay
from app.services.control_room.business_action_reservation import (
    ActionReservation,
    ReservationState,
)
from app.services.control_room.business_execution_approval import (
    execution_lifecycle_block,
)
from app.services.control_room.business_execution_precondition import (
    execution_authorization_contract,
)


ScopedRunner = Callable[[Callable[[Any], Awaitable[Any]]], Awaitable[Any]]
AsyncWriter = Callable[..., Awaitable[None]]
ResponseBuilder = Callable[..., dict[str, Any]]


async def _completed_replay(
    conn: Any,
    *,
    user: Mapping[str, Any],
    item: Mapping[str, Any],
    template_id: str,
) -> ActionReservation | None:
    _tenant_id, workspace_id = workspace_scope(user)
    replay = await matching_action_replay(
        conn,
        workspace_id=workspace_id,
        item=item,
        template_id=template_id,
        operation="execute",
        authorization_contract=execution_authorization_contract(user),
    )
    if replay is None:
        return None
    key, row = replay
    if str(row.get("status") or "") != "completed":
        return None
    return ActionReservation(
        id=int(row["id"]),
        effective_key=key,
        state=ReservationState.COMPLETED,
        row=dict(row),
    )


async def prepare_execution_entry(
    *,
    run_scoped: ScopedRunner,
    ensure_item_row: AsyncWriter,
    record_execute_block: AsyncWriter,
    response_for_reservation: ResponseBuilder,
    user: dict[str, Any],
    item: dict[str, Any],
    template: dict[str, Any],
    payload: dict[str, Any],
    confirmed: bool,
    ip: str | None,
    user_agent: str | None,
) -> dict[str, Any] | None:
    lifecycle_block = execution_lifecycle_block(item)
    if (
        lifecycle_block is not None
        and lifecycle_block.code == "already_executed"
        and confirmed
    ):
        reservation = await run_scoped(
            lambda conn: _completed_replay(
                conn,
                user=user,
                item=item,
                template_id=str(template["template_id"]),
            )
        )
        if reservation is not None:
            return response_for_reservation(
                reservation,
                item=item,
                payload=payload,
            )
    await run_scoped(
        lambda conn: ensure_item_row(
            conn,
            user=user,
            item=item,
            status=item.get("status") or "in_review",
            critical=True,
        )
    )
    if lifecycle_block is None:
        return None
    await run_scoped(
        lambda conn: record_execute_block(
            conn,
            user=user,
            item=item,
            template=template,
            payload=payload,
            ip=ip,
            user_agent=user_agent,
            message=lifecycle_block.message,
            error=lifecycle_block.code,
        )
    )
    raise HTTPException(409, lifecycle_block.message)


__all__ = ("prepare_execution_entry",)
