from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.services.control_room.business_action_approval import (
    approve_business_item,
    require_approvable_decision,
)
from app.services.control_room.business_runtime_evidence import (
    runtime_row_evidence_fields,
)
from app.services.control_room.business_workflow_provenance import (
    DECISION_PROVENANCE_KEY,
    WorkflowStage,
    workflow_eligibility_provenance,
)


USER = {
    "id": 7,
    "active_tenant_id": "tenant-a",
    "active_workspace_id": "workspace-a",
}


def _source_row(item: dict) -> dict:
    fields = (
        "id",
        "kind",
        "metric_name",
        "metric_type",
        "observed_value",
        "observation_date",
        "tenant_id",
        "workspace_id",
    )
    return {field: item[field] for field in fields}


def _item(**overrides) -> dict:
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
        "metric_name": "affected_people",
        "metric_type": "count",
        "observed_value": 1,
        "population_count": 10,
        "observation_date": "2026-07-20",
        **overrides,
    }
    return {
        **item,
        **runtime_row_evidence_fields(
            source_dataset=item["source_dataset"],
            source_system=item["source_system"],
            cartridge=item["cartridge"],
            tenant_id=item["tenant_id"],
            workspace_id=item["workspace_id"],
            source_row=_source_row(item),
            locator_field="id",
            observed_at=item["observation_date"],
        ),
    }


class DecisionLookup:
    def __init__(self, *, metadata: dict) -> None:
        self.metadata = metadata
        self.statements: list[str] = []

    async def fetchrow(self, sql: str, *_args):
        self.statements.append(" ".join(sql.split()))
        if "FROM decisions" in sql:
            return {"id": 91, "created_by_id": 7, "kpis": []}
        if "item_id <> $3" in sql:
            return None
        if "FROM control_room_items" in sql:
            return {
                "item_id": "business-1",
                "decision_id": 91,
                "owner_user_id": 7,
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
                "item_kind": "anomaly",
                "metadata": self.metadata,
            }
        raise AssertionError(sql)


@pytest.mark.asyncio
async def test_exact_existing_item_decision_link_is_accepted():
    item = _item()
    provenance = workflow_eligibility_provenance(
        item,
        stage=WorkflowStage.DECISION_CREATED,
        workspace_id="workspace-a",
        decision_id=91,
    )
    result = await require_approvable_decision(
        DecisionLookup(metadata={DECISION_PROVENANCE_KEY: provenance}),
        user=USER,
        item=item,
        workspace_id="workspace-a",
        decision_id=91,
    )

    assert result.linked is True
    assert result.row["id"] == 91


@pytest.mark.asyncio
async def test_exact_legacy_link_without_eligible_provenance_is_rejected():
    with pytest.raises(HTTPException) as exc:
        await require_approvable_decision(
            DecisionLookup(metadata={}),
            user=USER,
            item=_item(),
            workspace_id="workspace-a",
            decision_id=91,
        )

    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_foreign_workspace_provenance_is_rejected_when_live_item_omits_scope():
    live_item = _item()
    live_item.pop("workspace_id")
    provenance = workflow_eligibility_provenance(
        _item(workspace_id="workspace-b"),
        stage=WorkflowStage.DECISION_CREATED,
        workspace_id="workspace-b",
        decision_id=91,
    )

    with pytest.raises(HTTPException) as exc:
        await require_approvable_decision(
            DecisionLookup(metadata={DECISION_PROVENANCE_KEY: provenance}),
            user=USER,
            item=live_item,
            workspace_id="workspace-a",
            decision_id=91,
        )

    assert exc.value.status_code == 409


class QuarantineDuringApproval(DecisionLookup):
    def __init__(self, provenance: dict) -> None:
        super().__init__(metadata={})
        self.provenance = provenance
        self.item_reads = 0
        self.action_inserted = False

    async def fetchrow(self, sql: str, *_args):
        self.statements.append(" ".join(sql.split()))
        if "FROM decisions" in sql:
            return {"id": 91, "created_by_id": 7, "kpis": []}
        if "item_id <> $3" in sql:
            return None
        if "FROM control_room_items" in sql:
            self.item_reads += 1
            metadata = {DECISION_PROVENANCE_KEY: self.provenance}
            if self.item_reads > 1:
                metadata["workflow_quarantine"] = {"reason": "refresh"}
            return {
                "item_id": "business-1",
                "decision_id": 91,
                "owner_user_id": 7,
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
                "item_kind": "anomaly",
                "metadata": metadata,
            }
        if "INSERT INTO decision_actions" in sql:
            self.action_inserted = True
            return {"id": 1}
        raise AssertionError(sql)

    async def execute(self, sql: str, *_args):
        raise AssertionError(f"rejected approval performed DML: {sql}")


@pytest.mark.asyncio
async def test_approval_locks_and_revalidates_before_action_insert():
    item = _item()
    provenance = workflow_eligibility_provenance(
        item,
        stage=WorkflowStage.DECISION_CREATED,
        workspace_id="workspace-a",
        decision_id=91,
    )
    conn = QuarantineDuringApproval(provenance)

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
    assert conn.item_reads == 2
    assert "FOR UPDATE" in conn.statements[1]
    assert conn.action_inserted is False
