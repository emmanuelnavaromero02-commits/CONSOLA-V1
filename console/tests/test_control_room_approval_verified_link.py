from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services import control_room_service
from app.services.control_room.business_workflow_provenance import (
    DECISION_PROVENANCE_KEY,
    WorkflowStage,
    workflow_eligibility_provenance,
)


USER = {
    "id": 7,
    "email": "ops@example.com",
    "role": "user",
    "active_tenant_id": "tenant-A",
    "active_workspace_id": "workspace-A",
}


def _item() -> dict:
    return {
        "id": "item-1",
        "kind": "anomaly",
        "cartridge": "sap_hcm",
        "source_dataset": "employees_anomalies",
        "title": "Empleado terminado sigue activo",
        "recommendation": "Revisar baja.",
        "status": "open",
        "data_status": "ready",
        "metric_type": "count",
        "observed_value": 1,
        "population_count": 1,
        "observation_date": "2026-07-16",
        "evidence_refs": ["employees_anomalies:item-1"],
        "owner_user_id": 7,
    }


async def _scoped(pool, _user, work):
    return await work(pool, "tenant-A", "workspace-A")


class LinkedDecisionConnection:
    async def fetchrow(self, sql: str, *_args):
        if "FROM decisions" in sql:
            return {"id": 91, "created_by_id": 7, "kpis": []}
        if "item_id <> $3" in sql:
            return None
        if "FROM control_room_items" in sql:
            provenance = workflow_eligibility_provenance(
                _item(),
                stage=WorkflowStage.DECISION_CREATED,
                workspace_id="workspace-A",
                decision_id=91,
            )
            return {
                "item_id": "item-1",
                "decision_id": 91,
                "owner_user_id": 7,
                "item_kind": "anomaly",
                "metadata": {DECISION_PROVENANCE_KEY: provenance},
            }
        if "INSERT INTO decision_actions" in sql:
            return {"id": 3, "decision_id": 91, "ts": None}
        return None

    async def execute(self, sql: str, *_args):
        return "INSERT 0 1" if "INSERT INTO" in sql else "SELECT 1"


@pytest.mark.asyncio
async def test_approve_accepts_verified_decision_link_for_owned_item():
    conn = LinkedDecisionConnection()
    ensure = AsyncMock()
    approve_link = AsyncMock()
    link_decision = AsyncMock()
    audit = AsyncMock()
    with (
        patch.object(
            control_room_service, "_item_for_mutation", AsyncMock(return_value=_item())
        ),
        patch.object(control_room_service.auth, "pool", AsyncMock(return_value=conn)),
        patch.object(control_room_service, "_run_with_db_scope", _scoped),
        patch.object(control_room_service, "_ensure_item_row", ensure),
        patch.object(control_room_service, "link_control_room_decision", link_decision),
        patch.object(
            control_room_service, "approve_control_room_decision", approve_link
        ),
        patch.object(control_room_service, "_lessons_for_item", return_value=[]),
        patch.object(
            control_room_service, "_impact_for_item", return_value={"confidence": 0.9}
        ),
        patch.object(
            control_room_service,
            "_project_public_item",
            side_effect=lambda item, _builder, **updates: {**item, **updates},
        ),
        patch.object(control_room_service.audit_service, "record_event", audit),
    ):
        result = await control_room_service.approve_item("item-1", USER, decision_id=91)

    assert result["approved"] is True
    ensure.assert_awaited_once()
    assert ensure.await_args.kwargs["critical"] is True
    link_decision.assert_not_awaited()
    approve_link.assert_awaited_once()
    audit.assert_awaited_once()
