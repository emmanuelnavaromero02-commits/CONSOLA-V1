from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.domains.decisions.business_visibility import count_business_decisions
from app.services.control_room.business_runtime_evidence import (
    runtime_row_evidence_fields,
)
from app.services.control_room.business_workflow_provenance import (
    CURRENT_ELIGIBILITY_FINGERPRINT_KEY,
    DECISION_PROVENANCE_KEY,
    business_observation_fingerprint,
    decision_eligibility_provenance,
)


class _DecisionConnection:
    def __init__(self):
        now = datetime.now(UTC)
        business_metadata = {
            "data_status": "ready",
            "metric_type": "count",
            "observed_value": 1,
            "population_count": 1,
            "observation_date": "2026-07-16",
            "source_system": "sap",
            "cartridge": "sap",
        }
        business_metadata.update(
            runtime_row_evidence_fields(
                source_dataset="gold_metrics",
                source_system="sap",
                cartridge="sap",
                tenant_id="tenant-a",
                workspace_id="workspace-a",
                source_row={"item_id": "business-2", **business_metadata},
                locator_field="item_id",
                observed_at="2026-07-16",
            )
        )
        self.decisions = [
            {
                "id": decision_id,
                "created_at": now - timedelta(minutes=decision_id),
                "kpis": [],
            }
            for decision_id in (1, 2, 3, 4)
        ]
        self.items = [
            {
                "decision_id": 2,
                "item_id": "business-2",
                "item_kind": "anomaly",
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
                "source_dataset": "gold_metrics",
                "metadata": dict(business_metadata),
            },
            {
                "decision_id": 3,
                "item_id": "technical-3",
                "item_kind": "source_state",
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
                "source_dataset": "gold_metrics",
                "metadata": {"data_status": "missing"},
            },
        ]
        business = {
            "id": "business-2",
            "kind": "anomaly",
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "source_dataset": "gold_metrics",
            "metadata": dict(business_metadata),
        }
        fingerprint = business_observation_fingerprint(business)
        self.items[0]["metadata"].update(
            {
                CURRENT_ELIGIBILITY_FINGERPRINT_KEY: fingerprint,
                DECISION_PROVENANCE_KEY: decision_eligibility_provenance(
                    business, decision_id=2
                ),
            }
        )

    async def fetch(self, query: str, *_args):
        normalized = " ".join(query.split())
        if "FROM decisions" in normalized:
            return self.decisions
        if "FROM control_room_items" in normalized:
            return self.items
        if "FROM decision_actions" in normalized:
            return [{"decision_id": 4}]
        raise AssertionError(f"unexpected query: {normalized}")


@pytest.mark.asyncio
async def test_open_decision_count_includes_manual_and_eligible_linked_only():
    conn = _DecisionConnection()

    count = await count_business_decisions(
        conn,
        sql=(
            "SELECT * FROM decisions WHERE workspace_id = $1 "
            "AND status = 'open' ORDER BY created_at DESC, id DESC"
        ),
        params=["workspace-a"],
        workspace_id="workspace-a",
    )

    assert count == 2
