from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.services.control_room.business_command_item import resolve_command_item
from app.services.control_room.business_workflow_provenance import (
    WORKFLOW_QUARANTINE_KEY,
)


USER = {
    "id": 7,
    "role": "workspace_admin",
    "active_tenant_id": "tenant-a",
    "active_workspace_id": "workspace-a",
}


def _quarantined_diagnostic() -> dict:
    return {
        "id": "item-1",
        "kind": "source_state",
        "item_kind": "source_state",
        "source_dataset": "gold_people",
        "data_status": "missing",
        "metadata": {WORKFLOW_QUARANTINE_KEY: {"reason": "fingerprint_mismatch"}},
    }


def _live_business_item() -> dict:
    return {
        "id": "item-1",
        "kind": "anomaly",
        "cartridge": "sap_hcm",
        "source_dataset": "gold_people",
        "observed_value": 1,
        "metric_type": "count",
        "population_count": 10,
        "observation_date": "2026-07-21",
        "evidence_refs": ["gold_people:item-1"],
    }


@pytest.mark.asyncio
async def test_live_item_cannot_revive_persisted_quarantined_workflow():
    with pytest.raises(HTTPException) as exc:
        await resolve_command_item(
            "item-1",
            USER,
            load_persisted=AsyncMock(return_value=_quarantined_diagnostic()),
            collect_items=AsyncMock(
                return_value={"items": [_live_business_item()], "diagnostics": []}
            ),
            normalize_lineage=dict,
            pool_factory=AsyncMock(),
            run_scoped=AsyncMock(),
            projector=lambda item, **_kwargs: item,
        )

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "item_workflow_quarantined"
