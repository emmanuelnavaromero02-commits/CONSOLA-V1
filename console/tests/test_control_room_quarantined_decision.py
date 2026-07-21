from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.services.control_room.business_action_approval import (
    require_approvable_decision,
)
from app.services.control_room.business_workflow_provenance import (
    WORKFLOW_QUARANTINE_KEY,
)


class QuarantinedLink:
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
                "metadata": {WORKFLOW_QUARANTINE_KEY: {"reason": "mismatch"}},
            }
        raise AssertionError(sql)


@pytest.mark.asyncio
async def test_exact_link_cannot_approve_quarantined_workflow():
    item = {
        "id": "business-1",
        "kind": "anomaly",
        "workspace_id": "workspace-a",
        "source_dataset": "gold_people",
        "metric_type": "count",
        "observed_value": 1,
        "population_count": 10,
        "observation_date": "2026-07-20",
        "evidence_refs": ["gold_people:business-1"],
    }
    user = {
        "id": 7,
        "active_tenant_id": "tenant-a",
        "active_workspace_id": "workspace-a",
    }

    with pytest.raises(HTTPException) as exc:
        await require_approvable_decision(
            QuarantinedLink(),
            user=user,
            item=item,
            workspace_id="workspace-a",
            decision_id=91,
        )

    assert exc.value.status_code == 409
    assert exc.value.detail == "control room item has quarantined workflow"
