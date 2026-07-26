import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.services import control_room_service
from app.services.control_room.business_execution_precondition import (
    execution_authorization_contract,
    lock_pending_action_reservation,
)
from app.services.control_room.business_action_replay import action_reservation_contract


def _item() -> dict:
    return {
        "id": "item-1",
        "kind": "anomaly",
        "entity_kind": "employee",
        "entity_id": "employee-1",
        "decision_id": 42,
        "observed_value": 1,
        "metric_type": "count",
        "population_count": 10,
        "observation_date": "2026-07-20",
        "evidence_refs": ["gold_people:item-1"],
    }


def _user(revision: int) -> dict:
    return {
        "id": 7,
        "role": "admin",
        "access_revision": revision,
        "active_tenant_id": "tenant-a",
        "active_workspace_id": "workspace-a",
    }


@pytest.mark.asyncio
async def test_execute_block_guards_before_any_dml_when_item_became_diagnostic():
    guard = AsyncMock(
        side_effect=HTTPException(409, {"code": "item_business_state_changed"})
    )
    writes = {
        name: AsyncMock()
        for name in (
            "_record_action_execution",
            "_record_action_run",
            "_set_execution_status",
            "_record_item_event",
        )
    }
    with (
        patch.object(control_room_service, "lock_authoritative_business_item", guard),
        patch.object(
            control_room_service,
            "_record_action_execution",
            writes["_record_action_execution"],
        ),
        patch.object(
            control_room_service, "_record_action_run", writes["_record_action_run"]
        ),
        patch.object(
            control_room_service,
            "_set_execution_status",
            writes["_set_execution_status"],
        ),
        patch.object(
            control_room_service, "_record_item_event", writes["_record_item_event"]
        ),
        patch.object(
            control_room_service.audit_service, "record_event", AsyncMock()
        ) as audit,
    ):
        with pytest.raises(HTTPException) as exc:
            await control_room_service._record_execute_block(
                object(),
                user={
                    "id": 7,
                    "active_tenant_id": "tenant-a",
                    "active_workspace_id": "workspace-a",
                },
                item=_item(),
                template={"template_id": "create_followup_task"},
                payload={"mode": "execute_live"},
                message="blocked",
                error="explicit_confirmation_required",
                ip=None,
                user_agent=None,
            )

    assert exc.value.status_code == 409
    guard.assert_awaited_once()
    assert guard.await_args.kwargs["decision_id"] == 42
    for write in writes.values():
        write.assert_not_awaited()
    audit.assert_not_awaited()


@pytest.mark.asyncio
async def test_external_final_tx_locks_pending_reservation_and_access_revision():
    item = _item()
    contract = action_reservation_contract(
        workspace_id="workspace-a",
        item=item,
        template_id="create_followup_task",
        operation="execute",
        authorization_contract=execution_authorization_contract(_user(1)),
    )
    db = AsyncMock()
    db.fetchrow.return_value = {
        "id": 9,
        "status": "pending",
        "idempotency_key": "cr-action:v1:key",
        "metadata": json.dumps({"reservation_contract": contract}),
    }

    with pytest.raises(HTTPException) as exc:
        await lock_pending_action_reservation(
            db,
            user=_user(2),
            item=item,
            template_id="create_followup_task",
            reservation_id=9,
            effective_key="cr-action:v1:key",
        )

    assert exc.value.status_code == 409
    assert "FOR UPDATE" in db.fetchrow.await_args.args[0]
