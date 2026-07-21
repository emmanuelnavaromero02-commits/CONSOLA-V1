from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from app.services.control_room.business_eligibility import classify_business_item
from app.services.control_room.business_observation_codec import ENVELOPE_KEY
from app.services.control_room.business_workflow_provenance import (
    CURRENT_ELIGIBILITY_FINGERPRINT_KEY,
    ELIGIBILITY_POLICY_VERSION_KEY,
)
from app.services.intelligence.persistence import publish_control_room_item


def _artifact():
    return {
        "signal": {
            "signal_id": "signal-7",
            "cartridge_id": "sap",
            "domain": "People",
            "dataset": "metrics",
            "source_dataset": "metrics",
            "gold_table": "gold_metrics",
            "freshness_at": "2026-07-20",
            "summary": "Measured deviation",
            "severity": "high",
            "entity_kind": "employee",
            "entity_id": "employee-7",
            "entity_label": "Employee 7",
            "metric": "turnover",
            "metric_name": "Turnover",
            "actual_value": 3,
            "expected_value": 2,
            "deviation_value": 1,
            "deviation_pct": 0.5,
            "confidence": 0.9,
        },
        "evidence_pack": {
            "id": 17,
            "items": [{"source_ref": "metrics", "record_id": "record-7"}],
        },
        "hypotheses": [{"title": "Observed change"}],
        "options": [{"label": "Review", "option_id": "review"}],
    }


@pytest.mark.asyncio
async def test_intelligence_signal_persists_canonical_business_observation():
    pool = AsyncMock()
    pool.execute = AsyncMock()

    await publish_control_room_item(
        pool,
        "tenant-7",
        "workspace-7",
        {"id": 7, "email": "owner@example.com"},
        _artifact(),
    )

    args = pool.execute.await_args_list[0].args
    metadata = json.loads(args[15])
    claims = metadata[ENVELOPE_KEY]["claims"]
    persisted = {
        "id": args[4],
        "kind": args[8],
        "item_kind": args[8],
        "source_dataset": args[7],
        "metadata": metadata,
    }

    assert any(claim.get("metric_type") == "scalar" for claim in claims)
    assert any(claim.get("observed_value") == 3 for claim in claims)
    assert any(claim.get("observation_date") == "2026-07-20" for claim in claims)
    assert any(claim.get("evidence_refs") for claim in claims)
    assert CURRENT_ELIGIBILITY_FINGERPRINT_KEY in metadata
    assert ELIGIBILITY_POLICY_VERSION_KEY in metadata
    assert classify_business_item(persisted).eligible is True
