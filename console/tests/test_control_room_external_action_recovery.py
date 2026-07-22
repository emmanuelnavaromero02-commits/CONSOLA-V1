from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from app.services import control_room_service
from app.services.control_room.business_action_reservation import (
    ActionReservation,
    ReservationState,
    acquire_action_reservation,
)
from app.services.control_room.business_action_failure import (
    finalize_aborted_action_reservation,
)
from app.services.control_room.business_external_effect import (
    RemoteSideEffectCommitted,
)


def _item() -> dict:
    return {
        "id": "item-1",
        "kind": "anomaly",
        "workspace_id": "workspace-a",
        "decision_id": 42,
        "source_dataset": "gold_people",
        "observed_value": 1,
        "metric_type": "count",
        "population_count": 10,
        "observation_date": "2026-07-20",
    }


@pytest.mark.asyncio
async def test_stale_pending_reservation_is_reclaimed_with_same_effective_key():
    stale = datetime.now(UTC) - timedelta(minutes=10)
    db = AsyncMock()
    db.fetchrow.side_effect = [
        None,
        {"id": 7, "status": "pending", "updated_at": stale},
        {"id": 7, "status": "pending", "updated_at": datetime.now(UTC)},
    ]

    result = await acquire_action_reservation(
        db,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        item=_item(),
        template_id="external-template",
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
        patch.object(
            control_room_service, "lock_authoritative_business_item", AsyncMock()
        ),
        patch.object(control_room_service, "require_matching_dry_run", AsyncMock()),
        patch.object(
            control_room_service, "lock_pending_action_reservation", AsyncMock()
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
