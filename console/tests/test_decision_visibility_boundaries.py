from __future__ import annotations

import pytest

from app.domains.decisions.business_visibility import filter_decision_rows
from app.services.control_room.business_projection import (
    filter_business_decisions,
    normalize_persisted_business_item,
)
from app.services.control_room.business_runtime_evidence import (
    runtime_row_evidence_fields,
)
from app.services.control_room.business_workflow_provenance import (
    DECISION_PROVENANCE_KEY,
    WORKFLOW_QUARANTINE_KEY,
    WorkflowStage,
    workflow_eligibility_provenance,
)


def _linked_item(*, workspace_id: str = "workspace-a") -> dict:
    row = {
        "decision_id": 42,
        "item_id": "business-1",
        "item_kind": "anomaly",
        "cartridge_id": "sap_hcm",
        "source_dataset": "gold_people",
        "tenant_id": "tenant-a",
        "workspace_id": workspace_id,
        "owner_user_id": 7,
        "metadata": {
            "source_system": "sap_hcm",
            "data_status": "ready",
            "metric_type": "count",
            "observed_value": 1,
            "population_count": 10,
            "observation_date": "2026-07-20",
            **runtime_row_evidence_fields(
                source_dataset="gold_people",
                source_system="sap_hcm",
                cartridge="sap_hcm",
                tenant_id="tenant-a",
                workspace_id=workspace_id,
                source_row={"item_id": "business-1"},
                locator_field="item_id",
                observed_at="2026-07-20",
                business_observation={
                    "item_id": "business-1",
                    "item_kind": "anomaly",
                    "metric_type": "count",
                    "observed_value": 1,
                    "observation_date": "2026-07-20",
                },
            ),
        },
    }
    item = normalize_persisted_business_item(row)
    metadata = dict(row["metadata"])
    metadata[DECISION_PROVENANCE_KEY] = workflow_eligibility_provenance(
        item,
        stage=WorkflowStage.DECISION_CREATED,
        workspace_id=workspace_id,
        decision_id=42,
    )
    return {**row, "metadata": metadata}


class ScopedLinkedDecisionConnection:
    def __init__(self, linked: dict) -> None:
        self.linked = linked

    async def fetch(self, sql: str, *_args):
        normalized = " ".join(sql.split()).lower()
        if "from decision_actions" in normalized:
            return []
        if "decision_id = any" in normalized:
            selected = dict(self.linked)
            if "workspace_id" not in normalized.split("from", 1)[0]:
                selected.pop("workspace_id", None)
            return [selected]
        if "from control_room_items" in normalized:
            return []
        raise AssertionError(sql)


class LegacyEnglishControlRoomDecisionConnection:
    async def fetch(self, sql: str, *args):
        normalized = " ".join(sql.split()).lower()
        if "from control_room_items" in normalized:
            return []
        if "from decision_actions" in normalized:
            markers = args[2]
            if isinstance(markers, str):
                markers = [markers]
            return (
                [{"decision_id": 42}] if "Created from Control Room" in markers else []
            )
        raise AssertionError(sql)


@pytest.mark.asyncio
async def test_foreign_workspace_provenance_is_not_visible() -> None:
    linked = _linked_item(workspace_id="workspace-a")
    linked["metadata"][DECISION_PROVENANCE_KEY]["workspace_id"] = "workspace-b"
    conn = ScopedLinkedDecisionConnection(linked)

    visible = await filter_decision_rows(
        conn,
        workspace_id="workspace-a",
        tenant_id="tenant-a",
        rows=[{"id": 42}],
    )

    assert visible == []


@pytest.mark.asyncio
async def test_unlinked_legacy_english_control_room_decision_stays_hidden() -> None:
    visible = await filter_decision_rows(
        LegacyEnglishControlRoomDecisionConnection(),
        workspace_id="workspace-a",
        tenant_id="tenant-a",
        rows=[{"id": 42, "kpis": []}],
    )

    assert visible == []


def test_quarantined_workflow_is_not_projected_as_visible_decision() -> None:
    linked = _linked_item()
    linked["metadata"][WORKFLOW_QUARANTINE_KEY] = {
        "reason": "legacy_or_diagnostic_workflow"
    }

    assert filter_business_decisions([{"id": 42}], [linked]) == []
