from __future__ import annotations

import pytest

from app.services.control_room.business_workflow_provenance import (
    WorkflowStage,
    workflow_eligibility_provenance,
)
from control_room_runtime_evidence_fixture import bind_runtime_row_evidence


@pytest.mark.parametrize("stage", [WorkflowStage.APPROVED, WorkflowStage.EXECUTED])
def test_approved_and_executed_provenance_require_decision(stage):
    item = {
        "id": "business-1",
        "kind": "anomaly",
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "source_dataset": "gold_people",
        "source_system": "sap_hcm",
        "cartridge": "sap_hcm",
        "observed_value": 1,
        "metric_type": "count",
        "population_count": 10,
        "observation_date": "2026-07-20",
    }
    bind_runtime_row_evidence(
        item,
        locator_field="item_id",
        observed_at=item["observation_date"],
        source_row={"item_id": item["id"]},
    )
    with pytest.raises(ValueError, match="requires decision_id"):
        workflow_eligibility_provenance(
            item,
            stage=stage,
            workspace_id="workspace-a",
        )
