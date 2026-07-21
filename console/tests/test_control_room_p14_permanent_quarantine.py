from __future__ import annotations

import pytest

from app.services.control_room.business_runtime_evidence import (
    runtime_row_evidence_fields,
)
from app.services.control_room.business_workflow_provenance import (
    CURRENT_ELIGIBILITY_FINGERPRINT_KEY,
    DECISION_PROVENANCE_KEY,
    WORKFLOW_QUARANTINE_KEY,
    WorkflowStage,
    workflow_eligibility_provenance,
)
from app.services.control_room.business_workflow_reconciliation import (
    workflow_metadata_patches,
)


def _item(value: int = 1) -> dict:
    item = {
        "item_id": "business-1",
        "id": "business-1",
        "item_kind": "anomaly",
        "kind": "anomaly",
        "workspace_id": "workspace-a",
        "tenant_id": "tenant-a",
        "source_system": "sap_hcm",
        "cartridge": "sap_hcm",
        "source_dataset": "gold_people",
        "observed_value": value,
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
            source_row={"item_id": item["id"], "observed_value": value},
            locator_field="item_id",
            observed_at=item["observation_date"],
        ),
    }


class ExistingRow:
    def __init__(self, row: dict) -> None:
        self.row = row

    async def fetch(self, *_args):
        return [self.row]


class RefreshCycle(ExistingRow):
    async def refresh(self, item: dict, *, clear_workflow: bool = False) -> dict:
        patch = (await workflow_metadata_patches(self, [item]))[
            ("workspace-a", "business-1")
        ]
        self.row = {
            **self.row,
            **item,
            "decision_id": None if clear_workflow else self.row.get("decision_id"),
            "status": "open" if clear_workflow else self.row.get("status"),
            "selected_option_id": None,
            "execution_status": "not_started",
            "metadata": patch,
        }
        return patch


@pytest.mark.asyncio
async def test_legacy_quarantine_is_migrated_to_the_linked_generation():
    item = _item()
    provenance = workflow_eligibility_provenance(
        item,
        stage=WorkflowStage.DECISION_CREATED,
        workspace_id="workspace-a",
        decision_id=42,
    )
    quarantine = {"reason": "observation_changed", "policy_version": "v2"}
    row = {
        **item,
        "decision_id": 42,
        "status": "decision_created",
        "execution_status": "not_started",
        "metadata": {
            CURRENT_ELIGIBILITY_FINGERPRINT_KEY: provenance["fingerprint"],
            DECISION_PROVENANCE_KEY: provenance,
            WORKFLOW_QUARANTINE_KEY: quarantine,
        },
    }

    patch = (await workflow_metadata_patches(ExistingRow(row), [item]))[
        ("workspace-a", "business-1")
    ]

    generations = patch[WORKFLOW_QUARANTINE_KEY]["generations"]
    assert len(generations) == 1
    assert generations[0]["fingerprint"] == provenance["fingerprint"]
    assert generations[0]["decision_id"] == 42
    assert generations[0]["stage"] == "decision_created"
    assert generations[0]["reason"] == "observation_changed"


@pytest.mark.asyncio
async def test_modern_quarantine_survives_when_workflow_columns_are_cleared():
    item = _item()
    quarantine = {"reason": "observation_changed", "policy_version": "v2"}
    row = {
        **item,
        "decision_id": None,
        "status": "open",
        "selected_option_id": None,
        "execution_status": "not_started",
        "metadata": {WORKFLOW_QUARANTINE_KEY: quarantine},
    }

    patch = (await workflow_metadata_patches(ExistingRow(row), [item]))[
        ("workspace-a", "business-1")
    ]

    assert patch == {WORKFLOW_QUARANTINE_KEY: quarantine}


@pytest.mark.asyncio
async def test_workflow_a_never_revives_across_three_refreshes_of_observation_b():
    observation_a = _item(value=1)
    observation_b = _item(value=2)
    provenance = workflow_eligibility_provenance(
        observation_a,
        stage=WorkflowStage.DECISION_CREATED,
        workspace_id="workspace-a",
        decision_id=42,
    )
    state = RefreshCycle(
        {
            **observation_a,
            "decision_id": 42,
            "status": "decision_created",
            "execution_status": "not_started",
            "metadata": {
                CURRENT_ELIGIBILITY_FINGERPRINT_KEY: provenance["fingerprint"],
                DECISION_PROVENANCE_KEY: provenance,
            },
        }
    )

    patches = [
        await state.refresh(observation_b, clear_workflow=True),
        await state.refresh(observation_b),
        await state.refresh(observation_b),
    ]

    assert all(WORKFLOW_QUARANTINE_KEY in patch for patch in patches)
    assert all(DECISION_PROVENANCE_KEY not in patch for patch in patches)
    assert state.row["decision_id"] is None
    assert state.row["status"] == "open"
