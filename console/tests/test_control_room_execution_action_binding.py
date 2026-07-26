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
async def test_unapproved_workflow_stops_before_reservation_or_side_effect(template_id):
    item = _item(status="decision_created")
    authority = AsyncMock(
        side_effect=HTTPException(409, {"code": "workflow_approval_required"})
    )
    factory = Mock()
    external = template_id == "external_write"
    with ExitStack() as stack:
        _enter(
            stack,
            *_execution_patches(item, template_id),
            patch.object(
                control_room_service,
                "_writeback_capability",
                return_value={
                    "supported": True,
                    "external": external,
                    "adapter_available": True,
                },
            ),
            patch.object(
                control_room_service,
                "acquire_authoritative_action_reservation",
                authority,
            ),
            patch.object(
                control_room_service.WriteBackAdapterFactory,
                "get_adapter",
                factory,
            ),
            patch.object(
                control_room_service,
                "adapter_guarantees_idempotency",
                return_value=True,
            ),
            patch.object(
                control_room_service,
                "_external_writeback_enabled",
                return_value=True,
            ),
        )
        with pytest.raises(HTTPException) as exc:
            await control_room_service.execute_item(
                item["id"], USER, template_id=template_id, confirm_execute=True
            )

    assert exc.value.status_code == 409
    authority.assert_awaited_once()
    if external:
        factory.assert_called_once()
    else:
        factory.assert_not_called()
    factory.return_value.execute.assert_not_called()


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
    authority = Mock(
        item=item,
        template={"template_id": "external_write", "template_type": "external_write"},
        payload={"item": {"id": item["id"]}, "operations": []},
    )
    reserve = AsyncMock(return_value=Mock(context=authority, reservation=reservation))
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
                control_room_service,
                "acquire_authoritative_action_reservation",
                reserve,
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


@pytest.mark.asyncio
async def test_internal_replay_response_uses_locked_authoritative_context():
    stale = _item()
    locked = {**stale, "entity_id": "employee-authoritative"}
    template = {
        "template_id": "create_investigation_note",
        "template_type": "internal_investigation_note",
    }
    payload = {"item": {"entity_id": locked["entity_id"]}}
    authority = Mock(item=locked, template=template, payload=payload)
    reservation = ActionReservation(
        id=12,
        effective_key="server-key",
        state=ReservationState.IN_PROGRESS,
        row={"id": 12, "status": "pending"},
    )
    reserve = AsyncMock(return_value=Mock(context=authority, reservation=reservation))
    replay = Mock(return_value={"idempotent": True})

    with (
        patch.object(
            control_room_service,
            "acquire_authoritative_action_reservation",
            reserve,
        ),
        patch.object(control_room_service, "_reserved_action_response", replay),
    ):
        result = await control_room_service._execute_internal_investigation_note(
            object(),
            user=USER,
            item=stale,
            template=template,
            payload={"item": {"entity_id": stale["entity_id"]}},
            binding_id="binding-1",
            idempotency_key=None,
            ip=None,
            user_agent=None,
        )

    assert result == {"idempotent": True}
    assert replay.call_args.kwargs["item"] is locked
    assert replay.call_args.kwargs["payload"] is payload
