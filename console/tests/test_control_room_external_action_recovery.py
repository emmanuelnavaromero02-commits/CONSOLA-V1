from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.services import control_room_service
from app.services.control_room.business_action_reservation import (
    ActionReservation,
    ReservationState,
    acquire_action_reservation,
)
from app.services.control_room.business_action_replay import action_reservation_contract
from app.services.control_room.business_action_failure import (
    finalize_aborted_action_reservation,
)
from app.services.control_room.business_external_effect import (
    RemoteSideEffectCommitted,
    remote_effect_boundary,
)
from app.services.control_room.business_external_projection import (
    project_committed_external_effect,
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
        "details": {"pernr": "1001"},
        "metadata": {"connection": {"base_url": "https://h.invalid", "endpoint": "/r"}},
        "observed_value": 1,
        "metric_type": "count",
        "population_count": 10,
        "observation_date": "2026-07-20",
    }


def test_rejected_remote_result_does_not_claim_a_committed_side_effect():
    with pytest.raises(ConnectionError, match="local projection unavailable"):
        with remote_effect_boundary(
            {"ok": False, "executed": False},
            "remote-target",
            "IdempotentAdapter",
            {},
        ):
            raise ConnectionError("local projection unavailable")


@pytest.mark.asyncio
async def test_stale_pending_reservation_is_reclaimed_with_same_effective_key():
    stale = datetime.now(UTC) - timedelta(minutes=10)
    contract = action_reservation_contract(
        workspace_id="workspace-a",
        item=_item(),
        template_id="prepare_hcm_access_review",
        operation="execute",
        authorization_contract=None,
    )
    db = AsyncMock()
    db.fetchrow.side_effect = [
        None,
        {
            "id": 7,
            "status": "pending",
            "updated_at": stale,
            "metadata": {"reservation_contract": contract},
        },
        {"id": 7, "status": "pending", "updated_at": datetime.now(UTC)},
    ]

    result = await acquire_action_reservation(
        db,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        item=_item(),
        template_id="prepare_hcm_access_review",
        adapter_name="IdempotentAdapter",
        operation="execute",
    )

    assert result.state is ReservationState.ACQUIRED
    assert result.id == 7
    reclaim_sql = " ".join(db.fetchrow.await_args_list[2].args[0].split())
    assert "status = 'pending'" in reclaim_sql
    assert "updated_at" in reclaim_sql


@pytest.mark.asyncio
async def test_remote_success_late_local_failure_carries_durable_receipt():
    pool = AsyncMock()
    pool.fetchrow.return_value = {"id": 42, "status": "pending"}
    adapter = AsyncMock()
    adapter.supports_idempotency = True
    adapter.execute.return_value = {
        "ok": True,
        "status": "executed",
        "external_id": "ERP-42",
    }
    reservation = ActionReservation(
        id=42,
        effective_key="cr-action:v1:remote-success",
        state=ReservationState.ACQUIRED,
        row={},
    )

    with (
        patch.object(control_room_service, "require_approved_execution", AsyncMock()),
        patch.object(
            control_room_service,
            "lock_pending_action_reservation",
            AsyncMock(
                return_value={"metadata": {"remote_attempt": {"status": "started"}}}
            ),
        ),
        patch.object(
            control_room_service, "_record_writeback_audit_event", AsyncMock()
        ),
        patch.object(
            control_room_service.WriteBackAdapterFactory,
            "get_adapter",
            return_value=adapter,
        ),
        patch.object(
            control_room_service,
            "_record_action_execution",
            AsyncMock(side_effect=ConnectionError("local projection unavailable")),
        ),
    ):
        with pytest.raises(RemoteSideEffectCommitted) as exc:
            await control_room_service._execute_external_writeback(
                pool,
                user={
                    "id": 7,
                    "active_tenant_id": "tenant-a",
                    "active_workspace_id": "workspace-a",
                },
                item={**_item(), "tenant_id": "tenant-a"},
                template={
                    "template_id": "external-template",
                    "cartridge_id": "sap_hcm",
                },
                payload={},
                reservation=reservation,
                ip=None,
                user_agent=None,
            )

    assert exc.value.execution_result["executed"] is True
    assert exc.value.execution_result["adapter_result"]["external_id"] == "ERP-42"
    assert exc.value.side_effect["adapter"]
    assert adapter.execute.await_count == 1


@pytest.mark.asyncio
async def test_remote_success_receipt_finalizes_original_reservation_completed():
    db = AsyncMock()
    db.fetchrow.return_value = {"id": 42, "status": "completed"}
    reservation = ActionReservation(
        id=42,
        effective_key="cr-action:v1:remote-success",
        state=ReservationState.ACQUIRED,
        row={},
    )
    error = RemoteSideEffectCommitted(
        execution_result={"ok": True, "executed": True, "external_id": "ERP-42"},
        side_effect={"target": "sap", "adapter": "IdempotentAdapter"},
        cause=ConnectionError("local projection unavailable"),
    )

    with patch(
        "app.services.control_room.business_action_failure.project_committed_external_effect",
        AsyncMock(return_value=None),
    ):
        await finalize_aborted_action_reservation(
            db,
            workspace_id="workspace-a",
            reservation=reservation,
            error=error,
        )

    args = db.fetchrow.await_args.args
    assert args[4] == "completed"
    assert "ERP-42" in args[5]
    assert args[8] == "local_projection_failed_after_remote_success"


@pytest.mark.asyncio
async def test_remote_success_finalizer_marks_completed_after_exact_projection():
    db = AsyncMock()
    pending = {"id": 42, "status": "completed"}
    completed = {
        "id": 42,
        "status": "completed",
        "execution_result": {"local_projection_status": "completed"},
    }
    db.fetchrow.return_value = pending
    projection = AsyncMock(return_value=completed)
    reservation = ActionReservation(
        id=42,
        effective_key="cr-action:v1:remote-success",
        state=ReservationState.ACQUIRED,
        row={},
    )
    error = RemoteSideEffectCommitted(
        execution_result={"ok": True, "executed": True},
        side_effect={"target": "sap", "adapter": "IdempotentAdapter"},
        cause=ConnectionError("late local failure"),
    )

    with patch(
        "app.services.control_room.business_action_failure.project_committed_external_effect",
        projection,
    ):
        result = await finalize_aborted_action_reservation(
            db,
            workspace_id="workspace-a",
            reservation=reservation,
            error=error,
        )

    projection.assert_awaited_once_with(
        db,
        workspace_id="workspace-a",
        reservation_id=42,
        effective_key=reservation.effective_key,
    )
    assert result["execution_result"]["local_projection_status"] == "completed"


def test_pending_remote_receipt_replay_is_truthful_409():
    item = {**_item(), "execution_status": "dry_run_validated"}
    reservation = ActionReservation(
        id=42,
        effective_key="cr-action:v1:remote-success",
        state=ReservationState.COMPLETED,
        row={
            "id": 42,
            "status": "completed",
            "execution_result": {
                "ok": True,
                "executed": True,
                "local_projection_status": "pending_reconciliation",
            },
        },
    )

    with pytest.raises(HTTPException) as exc:
        control_room_service._reserved_action_response(
            reservation, item=item, payload={}
        )

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "external_action_pending_reconciliation"
    assert item["execution_status"] == "dry_run_validated"


@pytest.mark.asyncio
async def test_projection_contract_mismatch_keeps_durable_receipt_pending():
    db = AsyncMock()
    db.fetchrow.side_effect = [
        {
            "id": 42,
            "workspace_id": "workspace-a",
            "item_id": "item-1",
            "decision_id": 42,
            "status": "completed",
            "metadata": {
                "reservation_contract": {
                    "workspace_id": "different-workspace",
                    "item_id": "item-1",
                    "decision_id": 42,
                }
            },
            "execution_result": {
                "executed": True,
                "local_projection_status": "pending_reconciliation",
            },
        },
        {"item_id": "item-1", "decision_id": 42, "status": "approved"},
    ]

    result = await project_committed_external_effect(
        db,
        workspace_id="workspace-a",
        reservation_id=42,
        effective_key="cr-action:v1:remote-success",
    )

    assert result is None
    assert db.fetchrow.await_count == 2
