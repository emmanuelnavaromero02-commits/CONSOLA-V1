from __future__ import annotations

import json

import pytest

from tests.decision_orchestrator_harness import (
    FakeOrchestratorDB,
    _patch_pool,
    _user,
    orchestrator,
)


@pytest.mark.asyncio
async def test_orchestrator_uses_durable_wisdom_metrics_not_client_payload(
    orchestrator, monkeypatch
) -> None:
    db = FakeOrchestratorDB()
    _patch_pool(orchestrator, monkeypatch, db)
    normalized = []
    original_normalize = orchestrator.normalize_signal
    monkeypatch.setattr(
        orchestrator,
        "normalize_signal",
        lambda payload, source: normalized.append(original_normalize(payload, source))
        or normalized[-1],
    )
    user = _user(12)
    db.add_control_room_item(
        tenant_id=user["active_tenant_id"],
        workspace_id=user["active_workspace_id"],
        item_id="wb-talento-alert",
        item_kind="agent_alert",
        title="Durable WB-TALENTO monitor",
        source_dataset="sap_successfactors_talent_signals",
        metadata={
            "origin": "wisdom_bit",
            "agent_run_id": "agent-run-observed",
            "engine_run_id": "wb-run-observed",
            "description": "Durable aggregated monitor evidence.",
            "evidence_refs": [{"type": "wisdom_bit", "id": "WB-TALENTO"}],
            "analysis_evidence": {
                "engine": "wisdom_bit",
                "engine_run_id": "wb-run-observed",
                "metrics": {"risk_metric": "observed_readiness_delta"},
            },
        },
    )

    result = await orchestrator.orchestrate(
        user,
        {
            "source_type": "wisdom_bit",
            "source_id": "WB-TALENTO",
            "metrics": {"invented_client_metric": 999},
            "constraints": {"recommendation_only": True},
        },
    )

    run = result["orchestration"]
    assert run["source_type"] == "wisdom_bit"
    assert run["source_id"] == "WB-TALENTO"
    assert "invented_client_metric" not in json.dumps(run)
    assert normalized[0]["metrics"] == {"risk_metric": "observed_readiness_delta"}
