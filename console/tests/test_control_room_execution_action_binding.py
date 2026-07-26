from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import HTTPException

from app.services import control_room_service
from app.services.control_room.business_action_reservation import (
    ActionReservation,
    ReservationState,
)
from console.tests.test_control_room_execution_approval_lifecycle import (
    INTERNAL_TEMPLATES,
    USER,
    _execution_patches,
    _item,
)


def _enter(stack: ExitStack, *patches) -> None:
    for candidate in patches:
        stack.enter_context(candidate)


@pytest.mark.asyncio
@pytest.mark.parametrize("template_id", (*INTERNAL_TEMPLATES, "external_write"))
async def test_unapproved_workflow_stops_before_reservation_or_adapter(template_id):
    item = _item(status="decision_created")
    approval = AsyncMock(
        side_effect=HTTPException(409, {"code": "workflow_approval_required"})
    )
    reserve = AsyncMock()
    factory = Mock()
    internal = AsyncMock()
    with ExitStack() as stack:
        _enter(
            stack,
            *_execution_patches(item, template_id),
            patch.object(control_room_service, "require_approved_execution", approval),
            patch.object(
                control_room_service, "acquire_guarded_action_reservation", reserve
            ),
            patch.object(
                control_room_service.WriteBackAdapterFactory, "get_adapter", factory
            ),
            patch.object(
                control_room_service, "_execute_internal_followup_task", internal
            ),
        )
        with pytest.raises(HTTPException) as exc:
            await control_room_service.execute_item(
                item["id"], USER, template_id=template_id, confirm_execute=True
            )

    assert exc.value.status_code == 409
    approval.assert_awaited_once()
    reserve.assert_not_awaited()
    factory.assert_not_called()
    internal.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("template_id", INTERNAL_TEMPLATES)
async def test_approved_internal_workflow_dispatches(template_id):
    item = _item()
    target_name = {
        "create_followup_task": "_execute_internal_followup_task",
        "create_investigation_note": "_execute_internal_investigation_note",
        "mark_decision_for_monitoring": "_execute_internal_decision_monitoring",
    }[template_id]
    target = AsyncMock(return_value={"executed": True})
    with ExitStack() as stack:
        _enter(
            stack,
            *_execution_patches(item, template_id),
            patch.object(
                control_room_service, "require_approved_execution", AsyncMock()
            ),
            patch.object(
                control_room_service,
                "_writeback_capability",
                return_value={"supported": True, "external": False},
            ),
            patch.object(control_room_service, target_name, target),
        )
        result = await control_room_service.execute_item(
            item["id"], USER, template_id=template_id, confirm_execute=True
        )

    assert result["executed"] is True
    target.assert_awaited_once()


@pytest.mark.asyncio
async def test_approved_external_workflow_reaches_reserved_adapter_path(monkeypatch):
    monkeypatch.setenv("CONTROL_ROOM_ENABLE_EXTERNAL_WRITEBACK", "true")
    item = _item()
    reservation = ActionReservation(
        id=9,
        effective_key="server-key",
        state=ReservationState.ACQUIRED,
        row={"id": 9},
    )
    reserve = AsyncMock(return_value=reservation)
    run = AsyncMock(return_value={"executed": True})
    adapter = Mock(supports_idempotency=True)
    with ExitStack() as stack:
        _enter(
            stack,
            *_execution_patches(item, "external_write"),
            patch.object(
                control_room_service, "require_approved_execution", AsyncMock()
            ),
            patch.object(
                control_room_service,
                "_writeback_capability",
                return_value={
                    "supported": True,
                    "external": True,
                    "adapter_available": True,
                },
            ),
            patch.object(
                control_room_service.WriteBackAdapterFactory,
                "get_adapter",
                return_value=adapter,
            ),
            patch.object(
                control_room_service,
                "adapter_guarantees_idempotency",
                return_value=True,
            ),
            patch.object(
                control_room_service, "acquire_guarded_action_reservation", reserve
            ),
            patch.object(control_room_service, "run_reserved_external_action", run),
        )
        result = await control_room_service.execute_item(
            item["id"], USER, template_id="external_write", confirm_execute=True
        )

    assert result["executed"] is True
    reserve.assert_awaited_once()
    run.assert_awaited_once()
    assert callable(run.await_args.kwargs["prepare"])
