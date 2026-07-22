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
from app.services.control_room.business_execution_approval import (
    execution_lifecycle_block,
    require_approved_execution,
)
from app.services.control_room.business_projection import (
    normalize_persisted_business_item,
)
from app.services.control_room.business_workflow_provenance import (
    DECISION_PROVENANCE_KEY,
    ELIGIBILITY_POLICY_VERSION,
    WorkflowStage,
    business_observation_fingerprint,
)


USER = {
    "id": 7,
    "email": "owner@example.com",
    "active_tenant_id": "tenant-a",
    "active_workspace_id": "workspace-a",
}
INTERNAL_TEMPLATES = (
    "create_followup_task",
    "create_investigation_note",
    "mark_decision_for_monitoring",
)


def _item(*, status: str = "approved") -> dict:
    return {
        "id": "item-1",
        "kind": "anomaly",
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "owner_user_id": 7,
        "status": status,
        "decision_id": 42,
        "execution_status": "dry_run_validated",
        "source_dataset": "gold_people",
        "source_system": "test",
        "entity_id": "employee-1",
        "metric_type": "count",
        "observed_value": 1,
        "population_count": 10,
        "observation_date": "2026-07-20",
        "evidence_refs": ["gold_people:item-1"],
    }


def _row(item: dict, *, stage: WorkflowStage) -> dict:
    metadata = {
        key: item[key]
        for key in (
            "source_system",
            "metric_type",
            "observed_value",
            "population_count",
            "observation_date",
            "evidence_refs",
        )
    }
    row = {
        **item,
        "item_id": item["id"],
        "item_kind": item["kind"],
        "metadata": metadata,
    }
    normalized = normalize_persisted_business_item(row)
    metadata[DECISION_PROVENANCE_KEY] = {
        "policy_version": ELIGIBILITY_POLICY_VERSION,
        "stage": stage.value,
        "workspace_id": item["workspace_id"],
        "item_id": item["id"],
        "kind": item["kind"],
        "fingerprint": business_observation_fingerprint(normalized),
        "eligible_at_link": True,
        "decision_id": item["decision_id"],
    }
    return row


def test_terminal_workflows_remain_blocked():
    assert execution_lifecycle_block(_item(status="dismissed")).code == "terminal_item"
    assert execution_lifecycle_block(_item(status="resolved")).code == "terminal_item"
    executed = {**_item(), "execution_status": "executed"}
    assert execution_lifecycle_block(executed).code == "already_executed"


@pytest.mark.asyncio
async def test_current_decision_created_workflow_is_not_executable():
    item = _item(status="decision_created")
    dry_run = AsyncMock()
    with (
        patch(
            "app.services.control_room.business_execution_approval.lock_authoritative_business_item",
            AsyncMock(return_value=_row(item, stage=WorkflowStage.DECISION_CREATED)),
        ),
        patch(
            "app.services.control_room.business_execution_approval.require_matching_dry_run",
            dry_run,
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            await require_approved_execution(
                object(), user=USER, item=item, template_id="create_followup_task"
            )

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "workflow_approval_required"
    dry_run.assert_not_awaited()


@pytest.mark.asyncio
async def test_current_approved_workflow_requires_matching_dry_run():
    item = _item()
    dry_run = AsyncMock(return_value={"id": 11})
    with (
        patch(
            "app.services.control_room.business_execution_approval.lock_authoritative_business_item",
            AsyncMock(return_value=_row(item, stage=WorkflowStage.APPROVED)),
        ),
        patch(
            "app.services.control_room.business_execution_approval.require_matching_dry_run",
            dry_run,
        ),
    ):
        row = await require_approved_execution(
            object(), user=USER, item=item, template_id="create_followup_task"
        )

    assert row["status"] == "approved"
    dry_run.assert_awaited_once()


async def _scoped(_pool, _user, work):
    return await work(object(), "tenant-a", "workspace-a")


def _execution_patches(item: dict, template_id: str):
    template = {"template_id": template_id, "template_type": template_id}
    return (
        patch.object(
            control_room_service.auth, "pool", AsyncMock(return_value=object())
        ),
        patch.object(
            control_room_service, "_item_for_mutation", AsyncMock(return_value=item)
        ),
        patch.object(control_room_service, "_resolve_template", return_value=template),
        patch.object(
            control_room_service,
            "_execution_payload",
            return_value={"item": {"id": item["id"]}, "operations": []},
        ),
        patch.object(control_room_service, "_run_with_db_scope", side_effect=_scoped),
        patch.object(control_room_service, "_ensure_item_row", AsyncMock()),
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
    patches = _execution_patches(item, template_id)
    with ExitStack() as stack:
        _enter(
            stack,
            *patches,
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
    patches = _execution_patches(item, template_id)
    with ExitStack() as stack:
        _enter(
            stack,
            *patches,
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
    patches = _execution_patches(item, "external_write")
    with ExitStack() as stack:
        _enter(
            stack,
            *patches,
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
