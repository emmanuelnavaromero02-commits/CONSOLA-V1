from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.services import control_room_service
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
        patch.object(
            control_room_service,
            "require_explicit_action_template",
            return_value=template,
        ),
        patch.object(
            control_room_service,
            "_execution_payload",
            return_value={"item": {"id": item["id"]}, "operations": []},
        ),
        patch.object(control_room_service, "_run_with_db_scope", side_effect=_scoped),
        patch.object(
            control_room_service,
            "require_enabled_action_template",
            AsyncMock(),
        ),
        patch.object(control_room_service, "_ensure_item_row", AsyncMock()),
    )
