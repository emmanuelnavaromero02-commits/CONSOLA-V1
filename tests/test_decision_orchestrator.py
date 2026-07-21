from __future__ import annotations

import json
from typing import Any

import pytest

from app.services.control_room.business_runtime_evidence import (
    runtime_row_evidence_fields,
)
from tests.decision_orchestrator_harness import (
    FakeOrchestratorDB,
    _patch_pool,
    _user,
    orchestrator,
)


def test_classifier_routes_available_and_candidate_engines(orchestrator):
    resource = orchestrator.build_orchestration_plan(
        {
            "source_type": "manual_fixture",
            "source_id": "resource-1",
            "title": "Allocate staffing resources for forecast demand",
            "description": "Need allocation under constraints and capacity risk.",
            "constraints": {"hours": 120},
            "evidence_refs": [{"type": "control_room_item", "id": "x"}],
        },
        {"source_id": "resource-1"},
    )
    assert resource["problem_type"] == "resource_allocation"
    assert [item["name"] for item in resource["recommended_engines"]] == [
        "decision_intelligence",
        "monte_carlo",
    ]
    assert resource["candidate_engines"][0]["name"] == "constrained_optimizer_candidate"
    assert resource["engine_plan"]["candidate_engines_executed"] is False

    temporal = orchestrator.build_orchestration_plan(
        {
            "source_type": "manual_fixture",
            "source_id": "temporal-1",
            "title": "Real-time feedback control for delay risk",
            "description": "Dynamic trajectory with lead time and probability breach.",
            "time_horizon": "next 30 days",
            "evidence_refs": [{"type": "signal", "id": "s"}],
        },
        {"source_id": "temporal-1"},
    )
    assert temporal["problem_type"] == "temporal_control"
    assert temporal["candidate_engines"][0]["name"] == "mpc_candidate"

    strategy = orchestrator.build_orchestration_plan(
        {
            "source_type": "manual_fixture",
            "source_id": "strategy-1",
            "title": "Vendor negotiation against competitor response",
            "description": "Multi actor strategic market move.",
        },
        {"source_id": "strategy-1"},
    )
    assert strategy["problem_type"] == "multi_actor_strategy"
    assert strategy["candidate_engines"][0]["name"] == "game_theory_candidate"

    simple = orchestrator.build_orchestration_plan(
        {
            "source_type": "manual_fixture",
            "source_id": "action-1",
            "title": "Notify owner and create task",
            "description": "Escalate simple remediation.",
        },
        {"source_id": "action-1"},
    )
    assert simple["problem_type"] == "simple_action"
    assert simple["action_recommended"] is True
    assert "external_action_framework" in [
        item["name"] for item in simple["recommended_engines"]
    ]

    insufficient = orchestrator.build_orchestration_plan(
        {"source_type": "manual_fixture", "source_id": "empty"},
        {"source_id": "empty"},
    )
    assert insufficient["problem_type"] == "insufficient_data"
    assert insufficient["action_recommended"] is False


def test_orchestrator_preserves_market_context_evidence_without_execution(orchestrator):
    plan = orchestrator.build_orchestration_plan(
        {
            "source_type": "monte_carlo_simulation",
            "source_id": "mc-1",
            "title": "Forecast risk with external market context",
            "description": "Monte Carlo uncertainty includes governed Banxico context.",
            "evidence_refs": [{"type": "control_room_item", "id": "cri-1"}],
        },
        {
            "source_id": "mc-1",
            "metadata": {
                "evidence_refs": [
                    {
                        "type": "market_context",
                        "id": "banxico:usd_mxn_fix:2026-07-10:abc123",
                    }
                ]
            },
        },
    )

    refs = plan["evidence_refs"]
    assert {"type": "control_room_item", "id": "cri-1"} in refs
    assert {
        "type": "market_context",
        "id": "banxico:usd_mxn_fix:2026-07-10:abc123",
    } in refs
    assert (
        plan["decision_plan"]["external_evidence"]["market_context_policy"]
        == "evidence_only"
    )
    assert plan["engine_plan"]["external_evidence"]["market_context_ref_count"] == 1
    assert plan["engine_plan"]["available_engines_executed"] is False
    assert plan["action_recommended"] is False
    assert any("evidence only" in item for item in plan["safety_notes"])


@pytest.mark.asyncio
async def test_orchestrator_persists_with_scoped_runtime_and_tenant_isolation(
    orchestrator,
    monkeypatch,
):
    db = FakeOrchestratorDB()
    _patch_pool(orchestrator, monkeypatch, db)
    user_a = _user(10)
    user_b = _user(
        11,
        tenant_id="33333333-3333-3333-3333-333333333333",
        workspace_id="44444444-4444-4444-4444-444444444444",
    )
    db.add_control_room_item(
        tenant_id=user_a["active_tenant_id"],
        workspace_id=user_a["active_workspace_id"],
        item_id="item-a",
        title="Forecast anomaly probability breach",
        source_dataset="gold_metrics",
        metadata={
            "data_status": "ready",
            "observed_at": "2026-07-10T00:00:00Z",
            "metric_type": "scalar",
            "observed_value": 1,
            **runtime_row_evidence_fields(
                source_dataset="gold_metrics",
                source_row={"item_id": "item-a"},
                locator_field="item_id",
                observed_at="2026-07-10T00:00:00Z",
            ),
        },
    )

    result = await orchestrator.orchestrate(
        user_a,
        {"source_type": "control_room_item", "source_id": "item-a"},
    )
    run = result["orchestration"]
    assert run["problem_type"] == "risk_forecast"
    assert run["external_action_id"] is None
    assert db.scope_calls[-1] == (
        user_a["active_tenant_id"],
        user_a["active_workspace_id"],
    )

    listed = await orchestrator.list_orchestrations(
        user_a, source_type="control_room_item"
    )
    assert listed["orchestrations"][0]["orchestration_id"] == run["orchestration_id"]
    detail = await orchestrator.get_orchestration(user_a, run["orchestration_id"])
    assert detail["orchestration"]["orchestration_id"] == run["orchestration_id"]

    assert await orchestrator.list_orchestrations(user_b) == {"orchestrations": []}
    with pytest.raises(orchestrator.DecisionOrchestratorError) as hidden:
        await orchestrator.get_orchestration(user_b, run["orchestration_id"])
    assert hidden.value.status_code == 404

    with pytest.raises(orchestrator.DecisionOrchestratorError) as missing:
        await orchestrator.orchestrate(
            user_a,
            {"source_type": "control_room_item", "source_id": "missing"},
        )
    assert missing.value.status_code == 404


@pytest.mark.asyncio
async def test_orchestrator_accepts_wisdombit_monitor_source(orchestrator, monkeypatch):
    db = FakeOrchestratorDB()
    _patch_pool(orchestrator, monkeypatch, db)
    user = _user(12)

    result = await orchestrator.orchestrate(
        user,
        {
            "source_type": "wisdom_bit",
            "source_id": "WB-TALENTO",
            "title": "Decision operativa WB-TALENTO",
            "description": "Aggregated talent readiness monitor with blockers and simulation evidence.",
            "metrics": {"risk_metric": "talent_readiness_delta"},
            "constraints": {"recommendation_only": True},
            "evidence_refs": [{"type": "wisdom_bit", "id": "WB-TALENTO"}],
        },
    )

    run = result["orchestration"]
    assert run["source_type"] == "wisdom_bit"
    assert run["source_id"] == "WB-TALENTO"
    assert run["problem_type"] in {"risk_forecast", "data_quality"}


@pytest.mark.asyncio
async def test_orchestrator_rejects_payload_scope_and_manual_fixture_without_flag(
    orchestrator,
    monkeypatch,
):
    db = FakeOrchestratorDB()
    _patch_pool(orchestrator, monkeypatch, db)
    user = _user(20)

    with pytest.raises(orchestrator.DecisionOrchestratorError) as scope_error:
        await orchestrator.orchestrate(
            user,
            {
                "source_type": "control_room_item",
                "source_id": "item-a",
                "metrics": {"tenant_id": "malicious"},
            },
        )
    assert scope_error.value.status_code == 422

    with pytest.raises(orchestrator.DecisionOrchestratorError) as fixture_error:
        await orchestrator.orchestrate(
            user,
            {"source_type": "manual_fixture", "source_id": "fixture-1"},
        )
    assert fixture_error.value.status_code == 403


@pytest.mark.asyncio
async def test_orchestrator_optional_external_action_stays_pending_approval(
    orchestrator,
    monkeypatch,
):
    db = FakeOrchestratorDB()
    _patch_pool(orchestrator, monkeypatch, db)
    user = _user(30)
    db.add_control_room_item(
        tenant_id=user["active_tenant_id"],
        workspace_id=user["active_workspace_id"],
        item_id="action-source",
        title="Notify owner and create task",
        source_dataset="gold_metrics",
        metadata={
            "data_status": "ready",
            "observed_at": "2026-07-10T00:00:00Z",
            "metric_type": "scalar",
            "observed_value": 1,
            **runtime_row_evidence_fields(
                source_dataset="gold_metrics",
                source_row={"item_id": "action-source"},
                locator_field="item_id",
                observed_at="2026-07-10T00:00:00Z",
            ),
        },
    )
    propose_calls: list[dict[str, Any]] = []

    async def fake_propose(_user: dict, payload: dict[str, Any]) -> dict[str, Any]:
        propose_calls.append(payload)
        return {
            "action": {
                "id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                "status": "pending_approval",
                "adapter_name": "sandbox",
                "metadata": payload["metadata"],
                "execution_result": {},
            }
        }

    monkeypatch.setattr(orchestrator.external_actions, "propose", fake_propose)

    flag_off = await orchestrator.orchestrate(
        user,
        {"source_type": "control_room_item", "source_id": "action-source"},
    )
    assert flag_off["orchestration"]["action_recommended"] is True
    assert flag_off["orchestration"]["external_action_id"] is None
    assert propose_calls == []

    monkeypatch.setenv("DECISION_ORCHESTRATOR_CREATE_ACTIONS", "true")
    flag_on = await orchestrator.orchestrate(
        user,
        {"source_type": "control_room_item", "source_id": "action-source"},
    )
    run = flag_on["orchestration"]
    action = run["external_action"]
    assert run["external_action_id"] == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    assert action["status"] == "pending_approval"
    assert action["metadata"]["created_by_orchestrator"] is True
    assert action["metadata"]["orchestration_id"] == run["orchestration_id"]
    assert action["metadata"]["requires_human_approval"] is True
    assert action["execution_result"] == {}
    assert propose_calls[-1]["adapter_name"] == "sandbox"
    assert propose_calls[-1]["payload"]["orchestration_id"] == run["orchestration_id"]
    assert "tenant_id" not in json.dumps(propose_calls[-1]["payload"])
