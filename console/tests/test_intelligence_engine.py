from __future__ import annotations

import pytest

from app.services import intelligence_engine
from app.services.intelligence import engine as intelligence_engine_module
from app.services.intelligence import persistence as intelligence_persistence
from app.services.intelligence import readiness as intelligence_readiness_module


USER = {
    "id": 7,
    "email": "ops@example.com",
    "active_tenant_id": "11111111-1111-1111-1111-111111111111",
    "active_workspace_id": "22222222-2222-2222-2222-222222222222",
    "allowed_cartridges": ["hubspot"],
}


def _contract() -> dict:
    return {"cartridge": "hubspot", "domain": "Ventas"}


def _metric() -> dict:
    return {
        "id": "forecast_weighted",
        "name": "Forecast ponderado",
        "dataset": "forecast_mensual",
        "entity": {"kind": "seller", "id_field": "owner_id", "label_field": "vendedor"},
        "time_field": "mes",
        "value_field": "forecast_ponderado_usd",
        "expected_behavior": "higher_is_good",
        "baseline": {"method": "moving_average", "minimum_history": 2, "window": 6},
        "impact": {"currency": "USD", "unit_value": 20},
        "signal_rules": {"warning_pct": 0.20, "critical_pct": 0.45},
        "action_templates": [
            {
                "id": "inspect_pipeline",
                "label": "Revisar pipeline",
                "action_kind": "owner_review",
                "impact_multiplier": 0.50,
                "cost": 10,
                "risk": 5,
                "time_cost": 2,
            }
        ],
    }


def _second_metric() -> dict:
    metric = _metric()
    metric["id"] = "pipeline_value"
    metric["name"] = "Pipeline"
    metric["dataset"] = "pipeline_mensual"
    metric["value_field"] = "pipeline_usd"
    return metric


def _metric_with_prediction_and_external() -> dict:
    metric = _metric()
    metric["prediction"] = {
        "enabled": True,
        "method": "trend_delta",
        "horizon_days": [7, 21],
        "minimum_history": 2,
    }
    metric["external_sources"] = [
        {
            "id": "commercial_calendar",
            "type": "company_calendar",
            "events": [
                {
                    "entity_id": "u1",
                    "entity_kind": "seller",
                    "date": "2026-03-01",
                    "title": "Campana regional activa",
                    "strength": 0.72,
                }
            ],
        }
    ]
    metric["hypotheses"] = [
        {"id": "campaign_lift", "title": "Campana comercial activa"}
    ]
    return metric


def test_build_metric_artifacts_generates_baseline_signal_evidence_and_score():
    rows = [
        {
            "mes": "2026-01-01",
            "owner_id": "u1",
            "vendedor": "Sofia",
            "forecast_ponderado_usd": 100,
        },
        {
            "mes": "2026-02-01",
            "owner_id": "u1",
            "vendedor": "Sofia",
            "forecast_ponderado_usd": 120,
        },
        {
            "mes": "2026-03-01",
            "owner_id": "u1",
            "vendedor": "Sofia",
            "forecast_ponderado_usd": 110,
        },
        {
            "mes": "2026-04-01",
            "owner_id": "u1",
            "vendedor": "Sofia",
            "forecast_ponderado_usd": 200,
        },
    ]

    artifacts, skipped = intelligence_engine.build_metric_artifacts(
        _contract(), _metric(), rows
    )

    assert skipped == []
    assert len(artifacts) == 1
    artifact = artifacts[0]
    signal = artifact["signal"]
    baseline = artifact["baseline"]
    option = artifact["options"][0]
    decision = artifact["decision_intelligence"]

    assert signal["signal_type"] == "opportunity"
    assert signal["severity"] == "critical"
    assert signal["actual_value"] == 200
    assert signal["expected_value"] == 115
    assert baseline["sample_count"] == 3
    assert signal["decision_intelligence"] == decision
    assert decision["method"] == "robust_residual_v0"
    assert decision["anomaly_probability"] == 0.95
    assert decision["uncertainty_level"] == "high"
    assert decision["expected_impact"]["value"] == 1700
    assert decision["expected_impact"]["currency"] == "USD"
    assert decision["cost_of_delay"]["value_per_day"] == 56.67
    assert decision["recommended_decision"] == "investigate"
    assert decision["time_series"]["method"] == "robust_residual_v0"
    assert decision["time_series"]["residual"]["robust_z"] == 85
    assert {candidate["option"] for candidate in decision["options"]} == {
        "act_now",
        "investigate",
        "wait",
        "monitor",
    }
    assert decision["data_quality"] == {
        "history_points": 3,
        "minimum_required": 3,
        "status": "sufficient",
        "missing_fields": [],
    }
    assert artifact["evidence_pack"]["items"][0]["source_ref"] == "forecast_mensual"
    assert artifact["hypotheses"][0]["hypothesis_key"] == "baseline_deviation"
    assert option["score"] == pytest.approx(
        option["impact_expected"] * option["confidence"]
        - option["cost"]
        - option["risk"]
        - option["time_cost"]
    )


def test_build_metric_artifacts_applies_live_bayesian_calibration_metadata():
    rows = [
        {
            "mes": "2026-01-01",
            "owner_id": "u1",
            "vendedor": "Sofia",
            "forecast_ponderado_usd": 100,
        },
        {
            "mes": "2026-02-01",
            "owner_id": "u1",
            "vendedor": "Sofia",
            "forecast_ponderado_usd": 120,
        },
        {
            "mes": "2026-03-01",
            "owner_id": "u1",
            "vendedor": "Sofia",
            "forecast_ponderado_usd": 110,
        },
        {
            "mes": "2026-04-01",
            "owner_id": "u1",
            "vendedor": "Sofia",
            "forecast_ponderado_usd": 200,
        },
    ]
    group = "source_type:hubspot:forecast_weighted:v1"
    states = {
        group: {
            "calibration_group": group,
            "posterior": {"alpha": 12.0, "beta": 18.0, "mean": 0.4},
            "metrics": {
                "sample_count": 30,
                "confidence_score": 1.0,
                "complete": True,
                "provenance_complete": True,
                "prior_source": "global",
                "partial_pooling_applied": True,
                "parent_calibration_group": "global:forecast_weighted:v1",
                "parent_sample_count": 30,
            },
        }
    }

    artifacts, skipped = intelligence_engine.build_metric_artifacts(
        _contract(),
        _metric(),
        rows,
        calibration_states=states,
    )

    assert skipped == []
    decision = artifacts[0]["decision_intelligence"]
    metadata = decision["calibration"]
    assert metadata["raw_probability"] == pytest.approx(0.95)
    assert metadata["calibrated_probability"] == pytest.approx(0.75)
    assert metadata["calibration_applied"] is True
    assert metadata["partial_pooling_applied"] is True
    assert decision["anomaly_probability"] == metadata["calibrated_probability"]
    assert "not a calibrated Bayesian posterior" not in decision["rationale"]


def test_build_metric_artifacts_runs_template_monte_carlo_and_explicit_bayes_status():
    rows = [
        {
            "mes": "2026-01-01",
            "owner_id": "u1",
            "vendedor": "Sofia",
            "forecast_ponderado_usd": 100,
        },
        {
            "mes": "2026-02-01",
            "owner_id": "u1",
            "vendedor": "Sofia",
            "forecast_ponderado_usd": 120,
        },
        {
            "mes": "2026-03-01",
            "owner_id": "u1",
            "vendedor": "Sofia",
            "forecast_ponderado_usd": 110,
        },
        {
            "mes": "2026-04-01",
            "owner_id": "u1",
            "vendedor": "Sofia",
            "forecast_ponderado_usd": 200,
        },
    ]
    metric = {
        **_metric(),
        "simulation_template": {
            "id": "forecast_recovery_v1",
            "iterations": 250,
            "output_metric": "net_value",
            "breach_threshold": "$signal.expected_value",
            "breach_direction": "below",
            "input_variables": {
                "baseline_value": {"type": "fixed", "value": "$signal.expected_value"},
                "expected_delta": {
                    "type": "normal",
                    "mean": "$signal.deviation_value",
                    "stddev": 5,
                },
                "cost_per_day": {
                    "type": "fixed",
                    "value": "$decision.cost_of_delay.value_per_day",
                },
                "delay_days": {"type": "triangular", "low": 0, "mode": 1, "high": 7},
                "probability_of_delay": {"type": "fixed", "value": 0.5},
            },
        },
    }

    artifacts, skipped = intelligence_engine.build_metric_artifacts(
        _contract(), metric, rows
    )

    assert skipped == []
    artifact = artifacts[0]
    decision = artifact["decision_intelligence"]
    assert artifact["monte_carlo"]["status"] == "completed"
    assert artifact["monte_carlo"]["mode"] == "template_mode"
    assert artifact["monte_carlo"]["reproducibility_hash"]
    assert artifact["math_provenance"]["monte_carlo"]["mode"] == "template_mode"
    assert artifact["bayesian_calibration"]["status"] == "not_calibrated"
    assert artifact["bayesian_calibration"]["raw_probability"] == pytest.approx(
        decision["anomaly_probability"]
    )
    assert artifact["capabilities"]["bayesian_calibration"] == "not_calibrated"


def test_control_room_event_actor_id_accepts_numeric_strings_only():
    assert intelligence_persistence._actor_id(7) == 7
    assert intelligence_persistence._actor_id("7") == 7
    assert intelligence_persistence._actor_id(0) is None
    assert intelligence_persistence._actor_id("0") is None
    assert intelligence_persistence._actor_id("not-a-bigint") is None
    assert (
        intelligence_persistence._actor_id("11111111-1111-1111-1111-111111111111")
        is None
    )


def test_build_metric_artifacts_generates_predictive_signals_when_requested():
    rows = [
        {
            "mes": "2026-01-01",
            "owner_id": "u1",
            "vendedor": "Sofia",
            "forecast_ponderado_usd": 100,
        },
        {
            "mes": "2026-02-01",
            "owner_id": "u1",
            "vendedor": "Sofia",
            "forecast_ponderado_usd": 120,
        },
        {
            "mes": "2026-03-01",
            "owner_id": "u1",
            "vendedor": "Sofia",
            "forecast_ponderado_usd": 200,
        },
    ]

    artifacts, skipped = intelligence_engine.build_metric_artifacts(
        _contract(),
        _metric_with_prediction_and_external(),
        rows,
        horizon_days=[7, 21],
    )

    assert skipped == []
    predictive = [
        artifact
        for artifact in artifacts
        if artifact["signal"]["signal_subtype"].startswith("future_")
    ]
    assert {
        artifact["signal"]["prediction_horizon_days"] for artifact in predictive
    } == {7, 21}
    assert all(
        artifact["signal"]["predicted_value"] is not None for artifact in predictive
    )
    assert all(
        artifact["signal"]["confidence"] < artifacts[0]["signal"]["confidence"]
        for artifact in predictive
    )
    assert all(
        artifact["decision_intelligence"]["method"] == "future_reserved_state_space"
        for artifact in predictive
    )
    assert all(
        artifact["decision_intelligence"]["anomaly_probability"] is None
        for artifact in predictive
    )
    assert all(
        artifact["decision_intelligence"]["recommended_decision"]
        in {"investigate", "monitor"}
        for artifact in predictive
    )


def test_external_context_enriches_evidence_and_hypotheses_without_inventing_numbers():
    rows = [
        {
            "mes": "2026-01-01",
            "owner_id": "u1",
            "vendedor": "Sofia",
            "forecast_ponderado_usd": 100,
        },
        {
            "mes": "2026-02-01",
            "owner_id": "u1",
            "vendedor": "Sofia",
            "forecast_ponderado_usd": 120,
        },
        {
            "mes": "2026-03-01",
            "owner_id": "u1",
            "vendedor": "Sofia",
            "forecast_ponderado_usd": 200,
        },
    ]

    artifacts, _ = intelligence_engine.build_metric_artifacts(
        _contract(),
        _metric_with_prediction_and_external(),
        rows,
        include_external=True,
        horizon_days=[],
    )

    evidence_items = artifacts[0]["evidence_pack"]["items"]
    assert evidence_items[0]["data"]["row_count"] == 3
    assert evidence_items[0]["data"]["sample_hash"]
    assert any(item["source_type"] == "external" for item in evidence_items)
    assert any(
        hypothesis["hypothesis_key"] == "external_event_correlation"
        for hypothesis in artifacts[0]["hypotheses"]
    )


def test_build_metric_artifacts_does_not_invent_when_history_is_insufficient():
    rows = [
        {
            "mes": "2026-02-01",
            "owner_id": "u1",
            "vendedor": "Sofia",
            "forecast_ponderado_usd": 120,
        },
        {
            "mes": "2026-03-01",
            "owner_id": "u1",
            "vendedor": "Sofia",
            "forecast_ponderado_usd": 200,
        },
    ]

    artifacts, skipped = intelligence_engine.build_metric_artifacts(
        _contract(), _metric(), rows
    )

    assert artifacts == []
    assert skipped[0]["status"] == "insufficient_history"
    assert skipped[0]["minimum_history"] == 2
    decision = skipped[0]["decision_intelligence"]
    assert decision["method"] == "insufficient_history"
    assert decision["anomaly_probability"] is None
    assert decision["uncertainty_level"] == "high"
    assert decision["data_quality"]["history_points"] == 1
    assert decision["data_quality"]["minimum_required"] == 3
    assert decision["data_quality"]["status"] == "insufficient"


def test_decision_intelligence_high_probability_low_uncertainty_can_recommend_act_now():
    metric = _metric()
    metric["baseline"]["window"] = 8
    rows = [
        {
            "mes": f"period-{month:02d}",
            "owner_id": "u1",
            "vendedor": "Sofia",
            "forecast_ponderado_usd": 100,
        }
        for month in range(1, 9)
    ]
    rows.append(
        {
            "mes": "period-09",
            "owner_id": "u1",
            "vendedor": "Sofia",
            "forecast_ponderado_usd": 180,
        }
    )

    artifacts, skipped = intelligence_engine.build_metric_artifacts(
        _contract(), metric, rows
    )

    assert skipped == []
    decision = artifacts[0]["decision_intelligence"]
    assert decision["method"] == "robust_baseline_v0"
    assert decision["time_series"] is None
    assert decision["uncertainty_level"] == "low"
    assert decision["expected_impact"]["value"] == 1600
    assert decision["recommended_decision"] == "act_now"


def test_decision_intelligence_high_uncertainty_does_not_recommend_act_now():
    rows = [
        {
            "mes": "2026-01-01",
            "owner_id": "u1",
            "vendedor": "Sofia",
            "forecast_ponderado_usd": 100,
        },
        {
            "mes": "2026-02-01",
            "owner_id": "u1",
            "vendedor": "Sofia",
            "forecast_ponderado_usd": 500,
        },
        {
            "mes": "2026-03-01",
            "owner_id": "u1",
            "vendedor": "Sofia",
            "forecast_ponderado_usd": 50,
        },
        {
            "mes": "2026-04-01",
            "owner_id": "u1",
            "vendedor": "Sofia",
            "forecast_ponderado_usd": 1000,
        },
    ]

    artifacts, skipped = intelligence_engine.build_metric_artifacts(
        _contract(), _metric(), rows
    )

    assert skipped == []
    decision = artifacts[0]["decision_intelligence"]
    assert decision["method"] == "robust_residual_v0"
    assert decision["time_series"]["method"] == "robust_residual_v0"
    assert decision["uncertainty_level"] == "high"
    assert decision["recommended_decision"] in {"investigate", "monitor"}


def test_build_metric_artifacts_supports_global_entity_for_cross_cartridge_metrics():
    metric = _metric()
    metric["id"] = "capacity_gap"
    metric["dataset"] = "salesforce_forecast_vs_capacidad"
    metric["entity"] = {
        "kind": "pipeline",
        "id_field": "__all__",
        "label_field": "Forecast vs capacidad",
    }
    metric["value_field"] = "holgura_horas"
    rows = [
        {"mes": "2026-01-01", "holgura_horas": 100},
        {"mes": "2026-02-01", "holgura_horas": 100},
        {"mes": "2026-03-01", "holgura_horas": 40},
    ]

    artifacts, _ = intelligence_engine.build_metric_artifacts(
        {"cartridge": "salesforce", "domain": "Ventas"}, metric, rows
    )

    assert artifacts[0]["signal"]["entity_id"] == "__all__"
    assert artifacts[0]["signal"]["signal_type"] == "risk"
    assert artifacts[0]["signal"]["entity_label"] == "Forecast vs capacidad"


@pytest.mark.asyncio
async def test_run_intelligence_can_run_without_persisting_or_llm(monkeypatch):
    async def fake_fetcher(dataset: str, user: dict | None, limit: int):
        assert dataset == "forecast_mensual"
        assert user == USER
        assert limit >= 3
        return [
            {
                "mes": "2026-01-01",
                "owner_id": "u1",
                "vendedor": "Sofia",
                "forecast_ponderado_usd": 100,
            },
            {
                "mes": "2026-02-01",
                "owner_id": "u1",
                "vendedor": "Sofia",
                "forecast_ponderado_usd": 120,
            },
            {
                "mes": "2026-03-01",
                "owner_id": "u1",
                "vendedor": "Sofia",
                "forecast_ponderado_usd": 200,
            },
        ]

    monkeypatch.setattr(
        intelligence_engine,
        "load_contracts",
        lambda cartridge_ids=None: [{**_contract(), "metrics": [_metric()]}],
    )

    result = await intelligence_engine.run_intelligence(
        USER, {}, fetcher=fake_fetcher, persist=False
    )

    assert result["workspace_id"] == USER["active_workspace_id"]
    assert len(result["signals"]) == 1
    assert result["signals"][0]["signal_id"].startswith("intel:")


@pytest.mark.asyncio
async def test_run_intelligence_audits_duration_when_persisting(monkeypatch):
    events: list[dict] = []

    async def fake_fetcher(dataset: str, user: dict | None, limit: int):
        assert dataset == "forecast_mensual"
        return [
            {
                "mes": "2026-01-01",
                "owner_id": "u1",
                "vendedor": "Sofia",
                "forecast_ponderado_usd": 100,
            },
            {
                "mes": "2026-02-01",
                "owner_id": "u1",
                "vendedor": "Sofia",
                "forecast_ponderado_usd": 120,
            },
            {
                "mes": "2026-03-01",
                "owner_id": "u1",
                "vendedor": "Sofia",
                "forecast_ponderado_usd": 200,
            },
        ]

    async def fake_persist_artifacts(
        tenant_id,
        workspace_id,
        user,
        artifacts,
        *,
        intelligence_run_id=None,
        run_ref=None,
    ):
        assert tenant_id == USER["active_tenant_id"]
        assert workspace_id == USER["active_workspace_id"]
        assert artifacts
        assert intelligence_run_id == 42
        assert run_ref == "intel-run-test"

    async def fake_start_run(
        user, *, request, source_system, run_mode, datasets_evaluated, run_ref=None
    ):
        assert request == {}
        assert source_system is None
        assert run_mode == "manual"
        assert run_ref is None
        assert datasets_evaluated[0]["dataset"] == "forecast_mensual"
        return {"id": 42, "run_ref": "intel-run-test"}

    async def fake_finish_run(
        user,
        *,
        run_id,
        status,
        artifacts,
        skipped,
        datasets_evaluated,
        duration_ms,
        errors=None,
    ):
        assert run_id == 42
        assert status == "completed"
        assert len(artifacts) == 1
        assert skipped == []
        assert datasets_evaluated[0]["metric"] == "forecast_weighted"
        assert isinstance(duration_ms, int)
        return {"id": 42, "run_ref": "intel-run-test", "status": status}

    async def fake_record_event(
        user_id, email, action, resource_type, resource_id, metadata=None
    ):
        events.append(
            {
                "user_id": user_id,
                "email": email,
                "action": action,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "metadata": metadata or {},
            }
        )

    monkeypatch.setattr(
        intelligence_engine,
        "load_contracts",
        lambda cartridge_ids=None: [{**_contract(), "metrics": [_metric()]}],
    )
    monkeypatch.setattr(
        intelligence_engine_module, "persist_artifacts", fake_persist_artifacts
    )
    monkeypatch.setattr(
        intelligence_engine_module, "start_intelligence_run", fake_start_run
    )
    monkeypatch.setattr(
        intelligence_engine_module, "finish_intelligence_run", fake_finish_run
    )
    monkeypatch.setattr(
        intelligence_engine_module.audit_service, "record_event", fake_record_event
    )

    result = await intelligence_engine.run_intelligence(
        USER, {}, fetcher=fake_fetcher, persist=True
    )

    assert len(result["signals"]) == 1
    assert result["intelligence_run_id"] == 42
    assert result["run_ref"] == "intel-run-test"
    assert result["skipped_counts"] == {}
    assert events[0]["action"] == "intelligence.run"
    assert events[0]["metadata"]["intelligence_run_id"] == 42
    assert events[0]["metadata"]["run_ref"] == "intel-run-test"
    assert events[0]["metadata"]["run_mode"] == "manual"
    assert events[0]["metadata"]["signals"] == 1
    assert events[0]["metadata"]["skipped"] == 0
    assert isinstance(events[0]["metadata"]["duration_ms"], int)
    assert events[0]["metadata"]["duration_ms"] >= 0


@pytest.mark.asyncio
async def test_run_intelligence_persists_scheduled_run_counts_dataset_unavailable(
    monkeypatch,
):
    events: list[dict] = []
    finished: list[dict] = []

    async def failing_fetcher(dataset: str, user: dict | None, limit: int):
        raise RuntimeError("gold table missing")

    async def fake_start_run(
        user, *, request, source_system, run_mode, datasets_evaluated, run_ref=None
    ):
        assert request["run_mode"] == "scheduled"
        assert source_system == "hubspot"
        assert run_mode == "scheduled"
        assert run_ref is None
        assert datasets_evaluated[0]["dataset"] == "forecast_mensual"
        return {"id": 99, "run_ref": "intel-run-scheduled"}

    async def fake_finish_run(
        user,
        *,
        run_id,
        status,
        artifacts,
        skipped,
        datasets_evaluated,
        duration_ms,
        errors=None,
    ):
        finished.append(
            {
                "run_id": run_id,
                "status": status,
                "artifacts": artifacts,
                "skipped": skipped,
                "duration_ms": duration_ms,
            }
        )
        return {"id": run_id, "run_ref": "intel-run-scheduled", "status": status}

    async def fake_record_event(
        user_id, email, action, resource_type, resource_id, metadata=None
    ):
        events.append({"action": action, "metadata": metadata or {}})

    monkeypatch.setattr(
        intelligence_engine,
        "load_contracts",
        lambda cartridge_ids=None: [{**_contract(), "metrics": [_metric()]}],
    )
    monkeypatch.setattr(
        intelligence_engine_module, "start_intelligence_run", fake_start_run
    )
    monkeypatch.setattr(
        intelligence_engine_module, "finish_intelligence_run", fake_finish_run
    )
    monkeypatch.setattr(
        intelligence_engine_module.audit_service, "record_event", fake_record_event
    )

    result = await intelligence_engine.run_intelligence(
        USER,
        {"cartridge_id": "hubspot", "run_mode": "scheduled"},
        fetcher=failing_fetcher,
        persist=True,
    )

    assert result["signals"] == []
    assert result["run_mode"] == "scheduled"
    assert result["intelligence_run_id"] == 99
    assert result["dataset_unavailable_count"] == 1
    assert result["skipped_counts"] == {"dataset_unavailable": 1}
    assert finished[0]["status"] == "not_ready"
    assert finished[0]["skipped"][0]["status"] == "dataset_unavailable"
    assert events[0]["metadata"]["status"] == "not_ready"
    assert events[0]["metadata"]["dataset_unavailable_count"] == 1


@pytest.mark.asyncio
async def test_run_intelligence_gold_refresh_filters_datasets_and_uses_run_ref(
    monkeypatch,
):
    fetched: list[str] = []
    authority_steps: list[str] = []
    events: list[dict] = []

    async def fake_fetcher(dataset: str, user: dict | None, limit: int):
        fetched.append(dataset)
        assert user == USER
        return [
            {
                "mes": "2026-01-01",
                "owner_id": "u1",
                "vendedor": "Sofia",
                "forecast_ponderado_usd": 100,
            },
            {
                "mes": "2026-02-01",
                "owner_id": "u1",
                "vendedor": "Sofia",
                "forecast_ponderado_usd": 120,
            },
            {
                "mes": "2026-03-01",
                "owner_id": "u1",
                "vendedor": "Sofia",
                "forecast_ponderado_usd": 200,
            },
        ]

    async def fake_get_run_by_ref(user: dict, run_ref: str):
        assert run_ref == "gold-refresh:workspace-1:hubspot:dag-run-1"
        return None

    async def fake_start_run(
        user, *, request, source_system, run_mode, datasets_evaluated, run_ref=None
    ):
        assert request["run_mode"] == "gold_refresh"
        assert source_system == "hubspot"
        assert run_mode == "gold_refresh"
        assert run_ref == "gold-refresh:workspace-1:hubspot:dag-run-1"
        assert [item["dataset"] for item in datasets_evaluated] == ["forecast_mensual"]
        return {"id": 123, "run_ref": run_ref}

    async def unexpected_persist_artifacts(*_args, **_kwargs):
        raise AssertionError(
            "Gold refresh must use the outcome-binding persistence path"
        )

    async def fake_stage_gold_refresh_authority(
        user,
        *,
        expected_run_id,
        expected_run_ref,
        intelligence_run_id,
        publication_trace,
        expected_datasets,
    ):
        assert user == USER
        assert expected_run_id == "pipeline-run-1"
        assert expected_run_ref == "gold-refresh:workspace-1:hubspot:dag-run-1"
        assert intelligence_run_id == 123
        assert publication_trace is not None
        assert expected_datasets == ["forecast_mensual"]
        authority_steps.append("staged")

    async def fake_persist_gold_refresh_pending(
        user,
        *,
        run_id,
        run_ref,
        artifacts,
        skipped,
        datasets_evaluated,
        duration_ms,
    ):
        assert user == USER
        assert run_id == 123
        assert run_ref == "gold-refresh:workspace-1:hubspot:dag-run-1"
        assert all(
            artifact["signal"]["run_mode"] == "gold_refresh" for artifact in artifacts
        )
        assert skipped == []
        assert datasets_evaluated[0]["dataset"] == "forecast_mensual"
        assert duration_ms >= 0
        authority_steps.append("pending")
        return {"id": run_id, "run_ref": run_ref, "status": "binding_pending"}

    async def fake_persist_gold_refresh_binding(
        user,
        *,
        expected_run_id,
        expected_run_ref,
        intelligence_result,
        publication_trace,
        expected_datasets,
    ):
        assert user == USER
        assert expected_run_id == "pipeline-run-1"
        assert expected_run_ref == "gold-refresh:workspace-1:hubspot:dag-run-1"
        assert intelligence_result["intelligence_run_id"] == 123
        assert publication_trace is not None
        assert expected_datasets == ["forecast_mensual"]
        authority_steps.append("bound")

    async def unexpected_finish_run(*_args, **_kwargs):
        raise AssertionError("Gold refresh must not use the legacy finish path")

    async def fake_record_event(
        user_id, email, action, resource_type, resource_id, metadata=None
    ):
        events.append({"action": action, "metadata": metadata or {}})

    monkeypatch.setattr(
        intelligence_engine,
        "load_contracts",
        lambda cartridge_ids=None: [
            {**_contract(), "metrics": [_metric(), _second_metric()]}
        ],
    )
    monkeypatch.setattr(
        intelligence_engine_module, "get_run_by_ref", fake_get_run_by_ref
    )
    monkeypatch.setattr(
        intelligence_engine_module, "start_intelligence_run", fake_start_run
    )
    monkeypatch.setattr(
        intelligence_engine_module, "persist_artifacts", unexpected_persist_artifacts
    )
    monkeypatch.setattr(
        intelligence_engine_module,
        "stage_gold_refresh_authority",
        fake_stage_gold_refresh_authority,
    )
    monkeypatch.setattr(
        intelligence_engine_module,
        "persist_gold_refresh_pending",
        fake_persist_gold_refresh_pending,
    )
    monkeypatch.setattr(
        intelligence_engine_module,
        "persist_gold_refresh_binding",
        fake_persist_gold_refresh_binding,
    )
    monkeypatch.setattr(
        intelligence_engine_module, "finish_intelligence_run", unexpected_finish_run
    )
    monkeypatch.setattr(
        intelligence_engine_module.audit_service, "record_event", fake_record_event
    )

    result = await intelligence_engine.run_intelligence(
        USER,
        {
            "cartridge_id": "hubspot",
            "datasets": ["forecast_mensual"],
            "run_mode": "gold_refresh",
            "run_ref": "gold-refresh:workspace-1:hubspot:dag-run-1",
            "metadata": {"pipeline_run_id": "pipeline-run-1"},
        },
        fetcher=fake_fetcher,
        persist=True,
    )

    assert fetched == ["forecast_mensual"]
    assert result["run_mode"] == "gold_refresh"
    assert result["run_ref"] == "gold-refresh:workspace-1:hubspot:dag-run-1"
    assert result["idempotent"] is False
    assert result["datasets_requested"] == ["forecast_mensual"]
    assert authority_steps == ["staged", "pending", "bound"]
    assert result["skipped_counts"] == {}
    assert result["monte_carlo_counts"] == {"completed": 1}
    assert result["math_ruleset_version"] == "control_room_gold_signal.v1"
    assert events[0]["metadata"]["run_mode"] == "gold_refresh"
    assert events[0]["metadata"]["datasets_requested"] == ["forecast_mensual"]
    assert events[0]["metadata"]["monte_carlo_counts"] == {"completed": 1}


@pytest.mark.asyncio
async def test_run_intelligence_gold_refresh_infers_generic_signal_for_missing_contract(
    monkeypatch,
):
    async def fake_fetcher(dataset: str, user: dict | None, limit: int):
        assert dataset == "new_gold_dataset"
        assert user == USER
        assert limit >= 3
        return [
            {"mes": "2026-01-01", "account": "A", "amount": 100},
            {"mes": "2026-02-01", "account": "A", "amount": 110},
            {"mes": "2026-03-01", "account": "A", "amount": 220},
        ]

    monkeypatch.setattr(
        intelligence_engine,
        "load_contracts",
        lambda cartridge_ids=None: [{**_contract(), "metrics": [_metric()]}],
    )

    result = await intelligence_engine.run_intelligence(
        USER,
        {
            "cartridge_id": "hubspot",
            "datasets": ["new_gold_dataset"],
            "run_mode": "gold_refresh",
        },
        fetcher=fake_fetcher,
        persist=False,
    )

    assert result["generic_gold_signal_count"] == 1
    assert result["monte_carlo_counts"] == {"completed": 1}
    assert result["skipped_counts"]["missing_contract"] == 1
    artifact = result["artifacts"][0]
    assert artifact["control_origin"] == "generic_gold_signal"
    assert artifact["signal"]["control_origin"] == "generic_gold_signal"
    assert artifact["signal"]["dataset"] == "new_gold_dataset"
    assert artifact["signal"]["metric"] == "generic_amount"
    assert artifact["math_provenance"]["control_origin"] == "generic_gold_signal"
    assert artifact["monte_carlo"]["mode"] == "derived_mode"
    assert artifact["monte_carlo"]["reproducibility_hash"]


@pytest.mark.asyncio
async def test_run_intelligence_skips_generic_for_cross_sectional_snapshot(monkeypatch):
    # A per-employee snapshot (one row per user, single generated_at, no period)
    # must NOT be fabricated into a time series, and must never emit a signal on
    # the user_id identifier column (the production generic_user_id garbage).
    async def fake_fetcher(dataset: str, user: dict | None, limit: int):
        assert dataset == "talent_snapshot"
        return [
            {
                "generated_at": "2026-07-05 04:43:23",
                "user_id": 103169,
                "performance_100": 80,
            },
            {
                "generated_at": "2026-07-05 04:43:23",
                "user_id": 103252,
                "performance_100": 40,
            },
            {
                "generated_at": "2026-07-05 04:43:23",
                "user_id": 999999999,
                "performance_100": 10,
            },
        ]

    monkeypatch.setattr(
        intelligence_engine,
        "load_contracts",
        lambda cartridge_ids=None: [{**_contract(), "metrics": [_metric()]}],
    )

    result = await intelligence_engine.run_intelligence(
        USER,
        {
            "cartridge_id": "hubspot",
            "datasets": ["talent_snapshot"],
            "run_mode": "gold_refresh",
        },
        fetcher=fake_fetcher,
        persist=False,
    )

    assert result["generic_gold_signal_count"] == 0
    assert result["skipped_counts"].get("cross_sectional_no_timeseries") == 1
    assert all(
        artifact["signal"]["metric"] != "generic_user_id"
        for artifact in result["artifacts"]
    )


@pytest.mark.asyncio
async def test_run_intelligence_gold_refresh_run_ref_is_idempotent(monkeypatch):
    async def fake_get_run_by_ref(user: dict, run_ref: str):
        assert run_ref == "gold-refresh:workspace-1:hubspot:dag-run-1"
        return {
            "id": 321,
            "run_ref": run_ref,
            "status": "completed",
            "dataset_unavailable_count": 0,
            "insufficient_history_count": 0,
        }

    async def fail_start_run(*args, **kwargs):
        raise AssertionError("duplicate gold refresh should not start a new run")

    monkeypatch.setattr(
        intelligence_engine,
        "load_contracts",
        lambda cartridge_ids=None: [{**_contract(), "metrics": [_metric()]}],
    )
    monkeypatch.setattr(
        intelligence_engine_module, "get_run_by_ref", fake_get_run_by_ref
    )
    monkeypatch.setattr(
        intelligence_engine_module, "start_intelligence_run", fail_start_run
    )

    result = await intelligence_engine.run_intelligence(
        USER,
        {
            "cartridge_id": "hubspot",
            "datasets": ["forecast_mensual"],
            "run_mode": "gold_refresh",
            "run_ref": "gold-refresh:workspace-1:hubspot:dag-run-1",
        },
        persist=True,
    )

    assert result["idempotent"] is True
    assert result["signals"] == []
    assert result["intelligence_run_id"] == 321
    assert result["run_ref"] == "gold-refresh:workspace-1:hubspot:dag-run-1"


@pytest.mark.asyncio
async def test_run_intelligence_uses_scoped_gold_fetcher_by_default(monkeypatch):
    async def fake_gold_fetcher(dataset: str, user: dict | None, limit: int):
        assert dataset == "forecast_mensual"
        assert user == USER
        assert limit >= 3
        return [
            {
                "mes": "2026-01-01",
                "owner_id": "u1",
                "vendedor": "Sofia",
                "forecast_ponderado_usd": 100,
            },
            {
                "mes": "2026-02-01",
                "owner_id": "u1",
                "vendedor": "Sofia",
                "forecast_ponderado_usd": 120,
            },
            {
                "mes": "2026-03-01",
                "owner_id": "u1",
                "vendedor": "Sofia",
                "forecast_ponderado_usd": 200,
            },
        ]

    monkeypatch.setattr(
        intelligence_engine,
        "load_contracts",
        lambda cartridge_ids=None: [{**_contract(), "metrics": [_metric()]}],
    )
    monkeypatch.setattr(
        intelligence_engine_module, "query_intelligence_dataset_rows", fake_gold_fetcher
    )

    result = await intelligence_engine.run_intelligence(USER, {}, persist=False)

    assert len(result["signals"]) == 1
    assert result["skipped"] == []


@pytest.mark.asyncio
async def test_run_intelligence_rejects_requested_cartridge_outside_user_scope(
    monkeypatch,
):
    monkeypatch.setattr(
        intelligence_engine,
        "load_contracts",
        lambda cartridge_ids=None: [
            {"cartridge": "replicon", "domain": "Rentabilidad", "metrics": [_metric()]}
        ],
    )

    with pytest.raises(Exception) as exc:
        await intelligence_engine.run_intelligence(
            USER, {"cartridge_id": "replicon"}, persist=False
        )

    assert getattr(exc.value, "status_code", None) == 403
    assert "replicon" in str(getattr(exc.value, "detail", exc.value))


@pytest.mark.asyncio
async def test_run_intelligence_can_target_allowed_replicon_gold(monkeypatch):
    replicon_user = {**USER, "allowed_cartridges": ["replicon"]}

    async def fake_fetcher(dataset: str, user: dict | None, limit: int):
        assert dataset == "consultor_mensual"
        assert user == replicon_user
        assert limit >= 3
        return [
            {
                "mes": "2026-04-01",
                "consultor": "Andrea Morales",
                "horas_facturables": 124,
            },
            {
                "mes": "2026-05-01",
                "consultor": "Andrea Morales",
                "horas_facturables": 130,
            },
            {
                "mes": "2026-06-01",
                "consultor": "Andrea Morales",
                "horas_facturables": 40,
            },
        ]

    metric = {
        **_metric(),
        "id": "billable_hours",
        "name": "Horas facturables mensuales por consultor",
        "dataset": "consultor_mensual",
        "entity": {
            "kind": "consultant",
            "id_field": "consultor",
            "label_field": "consultor",
        },
        "time_field": "mes",
        "value_field": "horas_facturables",
        "expected_behavior": "higher_is_good",
    }
    monkeypatch.setattr(
        intelligence_engine,
        "load_contracts",
        lambda cartridge_ids=None: [
            {"cartridge": "replicon", "domain": "Rentabilidad", "metrics": [metric]}
        ],
    )

    result = await intelligence_engine.run_intelligence(
        replicon_user,
        {"cartridge_id": "replicon", "metrics": ["billable_hours"]},
        fetcher=fake_fetcher,
        persist=False,
    )

    assert len(result["signals"]) == 1
    assert result["signals"][0]["cartridge_id"] == "replicon"
    assert result["signals"][0]["dataset"] == "consultor_mensual"


@pytest.mark.asyncio
async def test_intelligence_readiness_sets_rls_context_before_signal_stats(monkeypatch):
    class FakeConnection:
        def __init__(self):
            self.executed: list[tuple[str, tuple]] = []

        async def execute(self, query: str, *args):
            self.executed.append((query, args))

        async def fetchrow(self, query: str, *args):
            assert any("app.workspace_id" in item[0] for item in self.executed)
            assert any("app.tenant_id" in item[0] for item in self.executed)
            return {"count": 3, "last_signal_at": None}

        def transaction(self):
            return self

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def close(self):
            return None

    fake = FakeConnection()

    async def fake_connect(*args, **kwargs):
        return fake

    monkeypatch.setattr(
        intelligence_readiness_module, "_operational_dsn", lambda: "postgresql://db"
    )
    monkeypatch.setattr(intelligence_readiness_module.asyncpg, "connect", fake_connect)

    stats = await intelligence_readiness_module._signal_stats(USER)

    assert stats["signal_count"] == 3


@pytest.mark.asyncio
async def test_intelligence_readiness_counts_gold_with_default_rls_scope(monkeypatch):
    class FakeOperationalConnection:
        async def fetchrow(self, query: str, *args):
            assert "FROM workspaces" in query
            return {
                "tenant_id": USER["active_tenant_id"],
                "workspace_id": USER["active_workspace_id"],
            }

        async def fetch(self, query: str, *args):
            assert "silver_lineage" in query
            return []

        async def close(self):
            return None

    class FakeTransaction:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

    class FakeGoldConnection:
        def __init__(self):
            self.executed: list[tuple[str, tuple]] = []

        def transaction(self):
            return FakeTransaction()

        async def execute(self, query: str, *args):
            self.executed.append((query, args))

        async def fetch(self, query: str, *args):
            assert "information_schema.columns" in query
            return [{"column_name": "tenant_id"}, {"column_name": "workspace_id"}]

        async def fetchval(self, query: str, *args):
            if "to_regclass" in query:
                return "public.gold_forecast_mensual"
            assert "tenant_id::text = $1" in query
            assert "workspace_id::text = $2" in query
            assert any("app.workspace_id" in item[0] for item in self.executed)
            assert any("app.tenant_id" in item[0] for item in self.executed)
            return 7

        async def close(self):
            return None

    async def fake_connect(dsn: str, *args, **kwargs):
        if dsn == "postgresql://operational":
            return FakeOperationalConnection()
        if dsn == "postgresql://gold":
            return FakeGoldConnection()
        raise AssertionError(f"unexpected dsn: {dsn}")

    monkeypatch.setattr(
        intelligence_readiness_module,
        "_operational_dsn",
        lambda: "postgresql://operational",
    )
    monkeypatch.setattr(
        intelligence_readiness_module, "_gold_dsn", lambda: "postgresql://gold"
    )
    monkeypatch.setattr(intelligence_readiness_module.asyncpg, "connect", fake_connect)

    rows = await intelligence_readiness_module._gold_counts(
        [
            {
                "dataset": "forecast_mensual",
                "table": "gold_forecast_mensual",
                "cartridges": ["hubspot"],
                "metrics": ["forecast_weighted"],
            }
        ],
        None,
    )

    assert rows[0]["status"] == "ready"
    assert rows[0]["row_count"] == 7
    assert rows[0]["scoped"] is True
