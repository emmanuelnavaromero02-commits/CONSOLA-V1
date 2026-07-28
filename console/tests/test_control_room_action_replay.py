from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.services.control_room.business_action_replay import (
    action_reservation_contract,
    matching_action_replay,
)
from app.services.control_room.business_action_reservation import (
    ReservationState,
    acquire_guarded_action_reservation,
)


def _item() -> dict:
    return {
        "id": "item-1",
        "kind": "anomaly",
        "workspace_id": "workspace-a",
        "entity_kind": "employee",
        "entity_id": "employee-1",
        "decision_id": 42,
        "source_dataset": "gold_people",
        "observed_value": 1,
        "metric_type": "count",
        "population_count": 10,
        "observation_date": "2026-07-20",
        "evidence_refs": ["gold_people:item-1"],
    }


def _user() -> dict:
    return {
        "id": 7,
        "email": "owner@example.com",
        "active_tenant_id": "tenant-a",
        "active_workspace_id": "workspace-a",
        "_effective_permissions": ["control_room.write", "control_room.execute"],
    }


@pytest.mark.asyncio
async def test_guarded_reservation_replays_only_existing_exact_action():
    row = {"id": 9, "status": "completed"}
    reserve = AsyncMock()
    with (
        patch(
            "app.services.control_room.business_action_reservation.require_approved_execution",
            side_effect=HTTPException(409, "workflow changed"),
        ),
        patch(
            "app.services.control_room.business_action_reservation.matching_action_replay",
            AsyncMock(return_value=("cr-action:v1:exact", row)),
        ),
        patch(
            "app.services.control_room.business_action_reservation.acquire_action_reservation",
            reserve,
        ),
    ):
        result = await acquire_guarded_action_reservation(
            object(),
            user=_user(),
            item=_item(),
            template_id="create_followup_task",
            adapter_name="internal_followup_task",
            operation="execute",
        )

    assert result.state is ReservationState.COMPLETED
    assert result.effective_key == "cr-action:v1:exact"
    reserve.assert_not_awaited()


@pytest.mark.asyncio
async def test_guarded_reservation_cannot_create_after_failed_guard():
    reserve = AsyncMock()
    with (
        patch(
            "app.services.control_room.business_action_reservation.require_approved_execution",
            side_effect=HTTPException(409, "workflow changed"),
        ),
        patch(
            "app.services.control_room.business_action_reservation.matching_action_replay",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.control_room.business_action_reservation.acquire_action_reservation",
            reserve,
        ),
    ):
        with pytest.raises(HTTPException, match="workflow changed"):
            await acquire_guarded_action_reservation(
                object(),
                user=_user(),
                item=_item(),
                template_id="create_followup_task",
                adapter_name="internal_followup_task",
                operation="execute",
            )

    reserve.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", (403, 404))
async def test_access_rejections_never_attempt_replay(status_code: int):
    replay = AsyncMock()
    reserve = AsyncMock()
    with (
        patch(
            "app.services.control_room.business_action_reservation.require_approved_execution",
            side_effect=HTTPException(status_code, "not visible"),
        ),
        patch(
            "app.services.control_room.business_action_reservation.matching_action_replay",
            replay,
        ),
        patch(
            "app.services.control_room.business_action_reservation.acquire_action_reservation",
            reserve,
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            await acquire_guarded_action_reservation(
                object(),
                user=_user(),
                item=_item(),
                template_id="create_followup_task",
                adapter_name="internal_followup_task",
                operation="execute",
            )

    assert exc.value.status_code == status_code
    replay.assert_not_awaited()
    reserve.assert_not_awaited()


@pytest.mark.asyncio
async def test_replay_rejects_mismatched_stored_contract():
    authorization = {"effective_permissions": ["control_room.execute"]}
    contract = action_reservation_contract(
        workspace_id="workspace-a",
        item=_item(),
        template_id="create_followup_task",
        operation="execute",
        authorization_contract=authorization,
    )
    contract["workspace_id"] = "workspace-b"
    db = AsyncMock()
    db.fetchrow.return_value = {
        "id": 9,
        "status": "completed",
        "metadata": json.dumps({"reservation_contract": contract}),
    }

    result = await matching_action_replay(
        db,
        workspace_id="workspace-a",
        item=_item(),
        template_id="create_followup_task",
        operation="execute",
        authorization_contract=authorization,
    )

    assert result is None
