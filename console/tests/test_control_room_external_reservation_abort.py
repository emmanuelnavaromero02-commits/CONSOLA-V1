from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.services import control_room_service
from app.services.control_room.business_action_reservation import (
    ActionReservation,
    ReservationState,
)


USER = {
    "id": 7,
    "email": "admin@example.com",
    "role": "admin",
    "active_tenant_id": "tenant-a",
    "active_workspace_id": "workspace-a",
}


def _item() -> dict:
    return {
        "id": "item-1",
        "kind": "anomaly",
        "decision_id": 42,
        "status": "in_review",
        "execution_status": "dry_run_validated",
        "metric_name": "headcount",
        "metric_type": "scalar",
        "observed_value": 7,
        "observation_date": "2026-07-20",
        "evidence_refs": ["gold_people:item-1"],
    }


@pytest.mark.asyncio
async def test_external_guard_abort_finalizes_durable_reservation_before_reraise():
    item = _item()
    template = {
        "template_id": "external-template",
        "type": "notification",
        "cartridge_id": "sap_hcm",
    }
    reservation = ActionReservation(
        id=91,
        effective_key="cr-action:v1:guard-abort",
        state=ReservationState.ACQUIRED,
        row={},
    )
    db = object()

    async def run_scoped(_pool, _user, operation):
        return await operation(db, "tenant-a", "workspace-a")

    finalizer = AsyncMock()
    adapter = AsyncMock()
    adapter.supports_idempotency = True
    with (
        patch.object(
            control_room_service,
            "_item_for_mutation",
            new=AsyncMock(return_value=item),
        ),
        patch.object(control_room_service, "_resolve_template", return_value=template),
        patch.object(control_room_service, "_execution_payload", return_value={}),
        patch.object(
            control_room_service.auth,
            "pool",
            new=AsyncMock(return_value=object()),
        ),
        patch.object(
            control_room_service,
            "_run_with_db_scope",
            new=AsyncMock(side_effect=run_scoped),
        ),
        patch.object(control_room_service, "_ensure_item_row", new=AsyncMock()),
        patch.object(
            control_room_service,
            "_writeback_capability",
            return_value={
                "external": True,
                "supported": True,
                "adapter_available": True,
            },
        ),
        patch.object(
            control_room_service, "_external_writeback_enabled", return_value=True
        ),
        patch.object(
            control_room_service.WriteBackAdapterFactory,
            "get_adapter",
            return_value=adapter,
        ),
        patch.object(
            control_room_service, "adapter_guarantees_idempotency", return_value=True
        ),
        patch.object(
            control_room_service,
            "acquire_guarded_action_reservation",
            new=AsyncMock(return_value=reservation),
        ),
        patch.object(
            control_room_service,
            "_execute_external_writeback",
            new=AsyncMock(
                side_effect=HTTPException(409, {"code": "item_business_state_changed"})
            ),
        ),
        patch.object(
            control_room_service,
            "finalize_aborted_action_reservation",
            new=finalizer,
            create=True,
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            await control_room_service.execute_item(
                item["id"],
                USER,
                template_id=template["template_id"],
                confirm_execute=True,
            )

    assert exc.value.status_code == 409
    finalizer.assert_awaited_once_with(
        db,
        workspace_id="workspace-a",
        reservation=reservation,
        error=exc.value,
    )
    adapter.execute.assert_not_called()
