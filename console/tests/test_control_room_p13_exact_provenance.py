from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.domains.decisions.business_visibility import (
    preserve_control_room_provenance,
)
from app.domains.decisions.provenance import decision_kpis_with_provenance
from app.services.control_room.business_action_approval import approve_business_item
from app.services.control_room.business_runtime_evidence import (
    runtime_row_evidence_fields,
)
from app.services.control_room.business_workflow_provenance import (
    DECISION_PROVENANCE_KEY,
    WorkflowStage,
    workflow_eligibility_provenance,
    workflow_has_eligible_provenance,
)


USER = {
    "id": 7,
    "email": "owner@example.com",
    "active_tenant_id": "tenant-a",
    "active_workspace_id": "workspace-a",
}


def _item() -> dict:
    item = {
        "id": "business-1",
        "kind": "anomaly",
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "title": "Measured anomaly",
        "recommendation": "Review",
        "source_dataset": "gold_people",
        "source_system": "sap_hcm",
        "cartridge": "sap_hcm",
        "observed_value": 1,
        "metric_type": "count",
        "population_count": 10,
        "observation_date": "2026-07-20",
    }
    return {
        **item,
        **runtime_row_evidence_fields(
            source_dataset="gold_people",
            source_system="sap_hcm",
            cartridge="sap_hcm",
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            source_row={"item_id": item["id"]},
            locator_field="item_id",
            observed_at=item["observation_date"],
        ),
    }


def _reserved_manual_marker() -> dict:
    return {
        "label": "Forged server marker",
        "provenance": {
            "type": "decision_provenance",
            "version": 1,
            "origin": "manual",
            "item_id": "business-1",
        },
    }


def test_option_selected_provenance_cannot_carry_decision_id():
    with pytest.raises(ValueError, match="must not include decision_id"):
        workflow_eligibility_provenance(
            {**_item(), "selected_option_id": "review"},
            stage=WorkflowStage.OPTION_SELECTED,
            workspace_id="workspace-a",
            decision_id=91,
            option_id="review",
        )


def test_decision_provenance_requires_an_explicit_stage():
    item = _item()
    provenance = workflow_eligibility_provenance(
        item,
        stage=WorkflowStage.DECISION_CREATED,
        workspace_id="workspace-a",
        decision_id=91,
    )
    provenance.pop("stage")

    assert not workflow_has_eligible_provenance(
        {DECISION_PROVENANCE_KEY: provenance},
        item,
        decision_id=91,
    )


def test_manual_post_sanitizes_every_reserved_decision_provenance_marker():
    kpis = decision_kpis_with_provenance(
        [{"label": "Revenue", "value": 10}, _reserved_manual_marker()],
        "manual",
    )

    markers = [
        entry.get("provenance")
        for entry in kpis
        if isinstance(entry, dict) and isinstance(entry.get("provenance"), dict)
    ]
    assert markers == [
        {"type": "decision_provenance", "version": 1, "origin": "manual"}
    ]


def test_manual_patch_sanitizes_every_reserved_decision_provenance_marker():
    assert preserve_control_room_provenance(
        [],
        [{"label": "Revenue", "value": 11}, _reserved_manual_marker()],
    ) == [{"label": "Revenue", "value": 11}]


class ExecutedDecisionLink:
    def __init__(self, provenance: dict) -> None:
        self.provenance = provenance
        self.action_inserted = False

    async def fetchrow(self, sql: str, *_args):
        if "FROM decisions" in sql:
            return {"id": 91, "created_by_id": 7}
        if "item_id <> $3" in sql:
            return None
        if "FROM control_room_items" in sql:
            return {
                "item_id": "business-1",
                "decision_id": 91,
                "owner_user_id": 7,
                "item_kind": "anomaly",
                "metadata": {DECISION_PROVENANCE_KEY: self.provenance},
            }
        if "INSERT INTO decision_actions" in sql:
            self.action_inserted = True
            return {"id": 1}
        raise AssertionError(sql)

    async def execute(self, sql: str, *_args):
        raise AssertionError(f"rejected approval performed DML: {sql}")


@pytest.mark.asyncio
async def test_approval_rejects_executed_stage_before_any_mutation():
    item = _item()
    provenance = workflow_eligibility_provenance(
        item,
        stage=WorkflowStage.EXECUTED,
        workspace_id="workspace-a",
        decision_id=91,
    )
    conn = ExecutedDecisionLink(provenance)

    with pytest.raises(HTTPException) as exc:
        await approve_business_item(
            conn,
            user=USER,
            item=item,
            workspace_id="workspace-a",
            decision_id=91,
            lessons=[],
            confidence=0.9,
            ensure_item_row=AsyncMock(),
            link_decision=AsyncMock(),
            approve_link=AsyncMock(),
        )

    assert exc.value.status_code == 409
    assert conn.action_inserted is False
