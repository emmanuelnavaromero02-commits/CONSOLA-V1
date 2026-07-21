from __future__ import annotations

import pytest

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
    return {
        "item_id": "business-1",
        "id": "business-1",
        "item_kind": "anomaly",
        "kind": "anomaly",
        "workspace_id": "workspace-a",
        "source_dataset": "gold_people",
        "observed_value": value,
        "metric_type": "count",
        "population_count": 10,
        "observation_date": "2026-07-20",
        "evidence_refs": ["gold_people:business-1"],
    }


class ExistingRow:
    def __init__(self, row: dict) -> None:
        self.row = row

    async def fetch(self, *_args):
        return [self.row]


@pytest.mark.asyncio
async def test_modern_quarantine_wins_over_matching_provenance_permanently():
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

    assert patch == {WORKFLOW_QUARANTINE_KEY: quarantine}


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
