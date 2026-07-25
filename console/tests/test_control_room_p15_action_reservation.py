from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.services.control_room.business_action_reservation import (
    ReservationState,
    acquire_action_reservation,
    acquire_guarded_action_reservation,
    complete_action_reservation,
    effective_action_key,
)
from app.services.control_room.business_execution_precondition import (
    DRY_RUN_CONTRACT_KEY,
    dry_run_metadata,
)
from app.services.control_room.business_workflow_provenance import (
    ELIGIBILITY_POLICY_VERSION,
)


def _item(**overrides):
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
        "evidence_refs": ["gold_people:item-1"],
        **overrides,
    }


def test_effective_key_binds_full_execution_contract_without_client_key():
    base = effective_action_key(
        workspace_id="workspace-a",
        item=_item(),
        template_id="create_followup_task",
        operation="execute",
    )
    assert base == effective_action_key(
        workspace_id="workspace-a",
        item=_item(),
        template_id="create_followup_task",
        operation="execute",
    )
    assert base == effective_action_key(
        workspace_id="workspace-a",
        item=_item(),
        template_id="create_followup_task",
        operation="execute",
        provided="retry-a",
    )
    assert base == effective_action_key(
        workspace_id="workspace-a",
        item=_item(),
        template_id="create_followup_task",
        operation="execute",
        provided="retry-b",
    )
    assert base != effective_action_key(
        workspace_id="workspace-b",
        item=_item(workspace_id="workspace-b"),
        template_id="create_followup_task",
        operation="execute",
    )
    assert base != effective_action_key(
        workspace_id="workspace-a",
        item=_item(decision_id=43),
        template_id="create_followup_task",
        operation="execute",
    )
    assert base != effective_action_key(
        workspace_id="workspace-a",
        item=_item(observed_value=2),
        template_id="create_followup_task",
        operation="execute",
    )
    assert base != effective_action_key(
        workspace_id="workspace-a",
        item=_item(),
        template_id="create_investigation_note",
        operation="execute",
    )


def test_dry_run_metadata_binds_policy_fingerprint_decision_and_template():
    contract = dry_run_metadata(_item(), template_id="create_followup_task")[
        DRY_RUN_CONTRACT_KEY
    ]

    assert contract["policy_version"] == ELIGIBILITY_POLICY_VERSION
    assert contract["item_id"] == "item-1"
    assert contract["decision_id"] == 42
    assert contract["template_id"] == "create_followup_task"
    assert contract["fingerprint"]


@pytest.mark.asyncio
async def test_atomic_reservation_returns_in_progress_to_loser():
    db = AsyncMock()
    db.fetchrow.side_effect = [None, {"id": 7, "status": "pending"}]

    result = await acquire_action_reservation(
        db,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        item=_item(),
        template_id="create_followup_task",
        adapter_name="internal_followup_task",
        operation="execute",
    )

    assert result.state is ReservationState.IN_PROGRESS
    assert result.id == 7
    sql = " ".join(db.fetchrow.await_args_list[0].args[0].split())
    assert "ON CONFLICT (workspace_id, idempotency_key) DO NOTHING" in sql
    assert "'pending'" in sql


@pytest.mark.asyncio
async def test_completion_is_cas_on_original_pending_reservation():
    db = AsyncMock()
    db.fetchrow.return_value = None

    with pytest.raises(Exception, match="reservation state changed"):
        await complete_action_reservation(
            db,
            workspace_id="workspace-a",
            reservation_id=7,
            effective_key="cr-action:v1:key",
            status="completed",
            execution_result={"ok": True},
            side_effect={"target": "decision_actions"},
        )

    sql = " ".join(db.fetchrow.await_args.args[0].split())
    assert "status = 'pending'" in sql


def test_registered_external_adapters_declare_idempotency_support():
    from app.services.adapters import WriteBackAdapterFactory

    assert WriteBackAdapterFactory.supports_idempotency("sap_hcm_it0008") is True
    assert (
        WriteBackAdapterFactory.supports_idempotency("prepare_billing_review") is True
    )


def test_custom_adapter_must_declare_idempotency_even_for_known_template():
    from app.services.adapter_idempotency import adapter_guarantees_idempotency

    class CustomAdapter:
        pass

    assert not adapter_guarantees_idempotency("prepare_billing_review", CustomAdapter())
    CustomAdapter.supports_idempotency = True
    assert adapter_guarantees_idempotency("prepare_billing_review", CustomAdapter())


def test_runtime_builtin_adapter_receives_idempotency_capability():
    from app.services import control_room_service
    from app.services.adapter_idempotency import adapter_guarantees_idempotency

    adapter = control_room_service.WriteBackAdapterFactory.get_adapter(
        "prepare_billing_review"
    )
    assert adapter_guarantees_idempotency("prepare_billing_review", adapter)


@pytest.mark.asyncio
async def test_guard_and_reservation_share_connection_and_order():
    db = object()
    order = []
    reservation = object()

    async def acquire(conn, **_kwargs):
        assert conn is db
        order.append("reserve")
        return reservation

    async def approved(conn, **kwargs):
        assert conn is db
        assert kwargs["template_id"] == "create_followup_task"
        order.append("approved")

    with (
        patch(
            "app.services.control_room.business_action_reservation.require_approved_execution",
            side_effect=approved,
        ),
        patch(
            "app.services.control_room.business_action_reservation.acquire_action_reservation",
            side_effect=acquire,
        ),
    ):
        result = await acquire_guarded_action_reservation(
            db,
            user={
                "id": 7,
                "email": "owner@example.com",
                "role": "admin",
                "active_tenant_id": "tenant-a",
                "active_workspace_id": "workspace-a",
            },
            item=_item(),
            template_id="create_followup_task",
            adapter_name="internal_followup_task",
            operation="execute",
        )

    assert result is reservation
    assert order == ["approved", "reserve"]


@pytest.mark.asyncio
async def test_dry_run_is_revalidated_after_lock_before_reservation():
    db = object()
    order = []

    async def stale_approval(conn, **kwargs):
        assert conn is db
        assert kwargs["template_id"] == "create_followup_task"
        order.append("approved")
        raise HTTPException(409, {"code": "matching_dry_run_required"})

    reserve = AsyncMock()
    with (
        patch(
            "app.services.control_room.business_action_reservation.require_approved_execution",
            side_effect=stale_approval,
        ),
        patch(
            "app.services.control_room.business_action_reservation.acquire_action_reservation",
            reserve,
        ),
        patch(
            "app.services.control_room.business_action_reservation.matching_action_replay",
            AsyncMock(return_value=None),
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            await acquire_guarded_action_reservation(
                db,
                user={
                    "id": 7,
                    "email": "owner@example.com",
                    "role": "admin",
                    "active_tenant_id": "tenant-a",
                    "active_workspace_id": "workspace-a",
                },
                item=_item(),
                template_id="create_followup_task",
                adapter_name="internal_followup_task",
                operation="execute",
            )

    assert exc.value.status_code == 409
    assert order == ["approved"]
    reserve.assert_not_awaited()
