from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.services.intelligence import decision_orchestrator, market_decision_validation


USER = {
    "id": 7,
    "active_tenant_id": "tenant-a",
    "active_workspace_id": "workspace-a",
    "allowed_cartridges": ["sap_successfactors", "banxico"],
}


def _source(*, status: str = "ready") -> dict:
    return {
        "source_id": "WB-TALENTO",
        "input_status": status,
        "source_mode": "benchmark_internal",
        "employee_count": 1288,
        "confidence": 0.6,
        "input_variables_json": json.dumps(
            {
                "baseline_value": {"type": "fixed", "value": 28},
                "expected_delta": {"type": "fixed", "value": -5},
                "delay_days": {"type": "triangular", "low": 1, "mode": 7, "high": 21},
                "cost_per_day": {"type": "fixed", "value": 1},
                "probability_of_delay": {"type": "fixed", "value": 0.6},
            }
        ),
        "assumptions_json": json.dumps({"basis": "aggregated"}),
        "evidence_refs_json": json.dumps(
            [{"type": "wisdom_bit", "id": "WB-TALENTO"}]
        ),
    }


@pytest.mark.asyncio
async def test_validation_runs_governed_flow_without_actions_or_fake_observations(
    monkeypatch,
):
    captured: dict = {}

    async def query_rows(dataset, user, limit=20):
        assert dataset == "sap_successfactors_talent_simulation_inputs"
        assert user == USER
        return [_source()]

    async def invoke(server, tool, args, *, user):
        assert (server, tool) == ("mcp-infra", "market_context_read")
        assert args["usable_only"] is True
        return {
            "context": [
                {
                    "provider": "banxico",
                    "metric_name": "usd_mxn_fix",
                    "as_of": "2026-07-10",
                    "usable": True,
                }
            ]
        }

    async def run_simulation(user, payload):
        captured["simulation_payload"] = payload
        assumptions = {
            **payload["assumptions"],
            "external_market_context": [
                {
                    "metric_name": "usd_mxn_fix",
                    "provider": "banxico",
                    "as_of": "2026-07-10",
                    "unit": "mxn_per_usd",
                    "confidence": "1.0",
                    "freshness_status": "ready",
                    "bounds": {"low": 17.1, "mode": 17.5, "high": 17.9},
                }
            ],
        }
        return {
            "simulation": {
                "simulation_id": "mc-e2e",
                "model_version": "sf_market_validation.v1",
                "output_metric": "cost",
                "assumptions": assumptions,
                "distribution_summary": {"p10": 40, "p50": 80, "p90": 180},
                "evidence_refs": [
                    {"type": "wisdom_bit", "id": "WB-TALENTO"},
                    {
                        "type": "market_context",
                        "id": "banxico:usd_mxn_fix:2026-07-10:abc",
                    },
                ],
            }
        }

    async def orchestrate(user, payload):
        captured["orchestration_payload"] = payload
        return {
            "orchestration": {
                "orchestration_id": "orch-e2e",
                "problem_type": "risk_forecast",
                "action_recommended": False,
                "external_action_id": None,
            }
        }

    async def get_state(user, **kwargs):
        captured["state_query"] = kwargs
        return {"states": []}

    async def forbidden_observe(*args, **kwargs):
        raise AssertionError("validation must not create calibration observations")

    monkeypatch.setattr(
        market_decision_validation, "query_intelligence_dataset_rows", query_rows
    )
    monkeypatch.setattr(market_decision_validation.mcp_registry, "invoke", invoke)
    monkeypatch.setattr(
        market_decision_validation.monte_carlo_service,
        "run_simulation",
        run_simulation,
    )
    monkeypatch.setattr(
        market_decision_validation.decision_orchestrator,
        "orchestrate",
        orchestrate,
    )
    monkeypatch.setattr(
        market_decision_validation.calibration_service, "get_state", get_state
    )
    monkeypatch.setattr(
        market_decision_validation.calibration_service, "observe", forbidden_observe
    )

    result = await market_decision_validation.run_validation(USER)

    simulation_payload = captured["simulation_payload"]
    assert simulation_payload["use_external_market_context"] is True
    assert simulation_payload["input_variables"]["cost_per_day"] == {
        "type": "external_market_context",
        "metric_name": "usd_mxn_fix",
        "uncertainty_pct": "0.02",
    }
    assert simulation_payload["output_metric"] == "cost"
    assert captured["orchestration_payload"]["constraints"] == {
        "recommendation_only": True,
        "no_external_writeback": True,
        "no_causal_claim": True,
        "no_pii": True,
    }
    assert result["status"] == "partial"
    assert result["simulation"]["market_evidence_count"] == 1
    assert result["bayes"]["status"] == "insufficient_data"
    assert result["policy"]["creates_calibration_observation"] is False
    assert result["policy"]["automatic_action"] is False
    serialized = json.dumps(result).lower()
    assert "source_url" not in serialized
    assert "token" not in serialized


@pytest.mark.asyncio
async def test_validation_stops_when_successfactors_inputs_are_not_ready(monkeypatch):
    async def query_rows(*args, **kwargs):
        return [_source(status="blocked")]

    monkeypatch.setattr(
        market_decision_validation, "query_intelligence_dataset_rows", query_rows
    )

    with pytest.raises(HTTPException) as exc:
        await market_decision_validation.run_validation(USER)

    assert exc.value.status_code == 422
    assert "not ready" in str(exc.value.detail)


def test_recommendation_only_constraint_disables_action_proposals():
    plan = decision_orchestrator.build_decision_plan(
        {
            "constraints": {
                "recommendation_only": True,
                "no_external_writeback": True,
            },
            "evidence_refs": [{"type": "market_context", "id": "banxico:usd_mxn"}],
        },
        {"problem_type": "simple_action", "confidence": 0.95},
        {"candidate_engines": []},
    )

    assert plan["recommendation_only"] is True
    assert plan["action_recommended"] is False
    assert plan["next_human_decision"] == "review_or_collect_more_evidence"


def test_validation_report_is_ready_only_with_real_cpa_and_bayes_state():
    source = {**_source(), "source_mode": "cpa_real"}
    simulation = {
        "simulation_id": "mc-real",
        "model_version": "sf_market_validation.v1",
        "output_metric": "cost",
        "assumptions": {
            "external_market_context": [
                {
                    "metric_name": "usd_mxn_fix",
                    "as_of": "2026-07-10",
                    "freshness_status": "ready",
                }
            ]
        },
        "evidence_refs": [{"type": "market_context", "id": "banxico:usd_mxn"}],
    }
    orchestration = {
        "orchestration_id": "orch-real",
        "action_recommended": False,
        "external_action_id": None,
    }

    report = market_decision_validation._report(  # noqa: SLF001
        source,
        simulation,
        orchestration,
        [{"sample_count": 5}],
    )

    assert report["status"] == "ready"
    assert report["source"]["source_mode"] == "cpa_real"


@pytest.mark.asyncio
async def test_validation_requires_explicit_workspace_cartridge_scope_in_production(
    monkeypatch,
):
    monkeypatch.setenv("APP_ENV", "production")

    with pytest.raises(HTTPException) as exc:
        await market_decision_validation._source_snapshot(  # noqa: SLF001
            {
                "id": 7,
                "active_tenant_id": "tenant-a",
                "active_workspace_id": "workspace-a",
            }
        )

    assert exc.value.status_code == 403
    assert exc.value.detail == "workspace cartridge scope is required"


def test_control_room_validation_routes_keep_read_and_write_guards():
    source = (
        Path(__file__).resolve().parents[1] / "console/app/routers/control_room.py"
    ).read_text(encoding="utf-8")
    read_route = source.split(
        '"/sap-successfactors/market-validation",', 1
    )[1].split("async def control_room_sap_successfactors_market_validation", 1)[0]
    run_route = source.split(
        '"/sap-successfactors/market-validation/run",', 1
    )[1].split("async def control_room_sap_successfactors_market_validation_run", 1)[0]

    assert 'Depends(require_permission("datasets.read"))' in read_route
    assert "Depends(require_csrf)" not in read_route
    assert "Depends(require_csrf)" in run_route
    assert 'Depends(require_permission("control_room.write"))' in run_route
