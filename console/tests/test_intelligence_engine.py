from __future__ import annotations

import pytest

from app.services import intelligence_engine


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


def _metric_with_prediction_and_external() -> dict:
    metric = _metric()
    metric["prediction"] = {"enabled": True, "method": "trend_delta", "horizon_days": [7, 21], "minimum_history": 2}
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
    metric["hypotheses"] = [{"id": "campaign_lift", "title": "Campana comercial activa"}]
    return metric


def test_build_metric_artifacts_generates_baseline_signal_evidence_and_score():
    rows = [
        {"mes": "2026-01-01", "owner_id": "u1", "vendedor": "Sofia", "forecast_ponderado_usd": 100},
        {"mes": "2026-02-01", "owner_id": "u1", "vendedor": "Sofia", "forecast_ponderado_usd": 120},
        {"mes": "2026-03-01", "owner_id": "u1", "vendedor": "Sofia", "forecast_ponderado_usd": 200},
    ]

    artifacts, skipped = intelligence_engine.build_metric_artifacts(_contract(), _metric(), rows)

    assert skipped == []
    assert len(artifacts) == 1
    artifact = artifacts[0]
    signal = artifact["signal"]
    baseline = artifact["baseline"]
    option = artifact["options"][0]

    assert signal["signal_type"] == "opportunity"
    assert signal["severity"] == "critical"
    assert signal["actual_value"] == 200
    assert signal["expected_value"] == 110
    assert baseline["sample_count"] == 2
    assert artifact["evidence_pack"]["items"][0]["source_ref"] == "forecast_mensual"
    assert artifact["hypotheses"][0]["hypothesis_key"] == "baseline_deviation"
    assert option["score"] == pytest.approx(
        option["impact_expected"] * option["confidence"] - option["cost"] - option["risk"] - option["time_cost"]
    )


def test_build_metric_artifacts_generates_predictive_signals_when_requested():
    rows = [
        {"mes": "2026-01-01", "owner_id": "u1", "vendedor": "Sofia", "forecast_ponderado_usd": 100},
        {"mes": "2026-02-01", "owner_id": "u1", "vendedor": "Sofia", "forecast_ponderado_usd": 120},
        {"mes": "2026-03-01", "owner_id": "u1", "vendedor": "Sofia", "forecast_ponderado_usd": 200},
    ]

    artifacts, skipped = intelligence_engine.build_metric_artifacts(
        _contract(),
        _metric_with_prediction_and_external(),
        rows,
        horizon_days=[7, 21],
    )

    assert skipped == []
    predictive = [artifact for artifact in artifacts if artifact["signal"]["signal_subtype"].startswith("future_")]
    assert {artifact["signal"]["prediction_horizon_days"] for artifact in predictive} == {7, 21}
    assert all(artifact["signal"]["predicted_value"] is not None for artifact in predictive)
    assert all(artifact["signal"]["confidence"] < artifacts[0]["signal"]["confidence"] for artifact in predictive)


def test_external_context_enriches_evidence_and_hypotheses_without_inventing_numbers():
    rows = [
        {"mes": "2026-01-01", "owner_id": "u1", "vendedor": "Sofia", "forecast_ponderado_usd": 100},
        {"mes": "2026-02-01", "owner_id": "u1", "vendedor": "Sofia", "forecast_ponderado_usd": 120},
        {"mes": "2026-03-01", "owner_id": "u1", "vendedor": "Sofia", "forecast_ponderado_usd": 200},
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
    assert any(hypothesis["hypothesis_key"] == "external_event_correlation" for hypothesis in artifacts[0]["hypotheses"])


def test_build_metric_artifacts_does_not_invent_when_history_is_insufficient():
    rows = [
        {"mes": "2026-02-01", "owner_id": "u1", "vendedor": "Sofia", "forecast_ponderado_usd": 120},
        {"mes": "2026-03-01", "owner_id": "u1", "vendedor": "Sofia", "forecast_ponderado_usd": 200},
    ]

    artifacts, skipped = intelligence_engine.build_metric_artifacts(_contract(), _metric(), rows)

    assert artifacts == []
    assert skipped[0]["status"] == "insufficient_history"
    assert skipped[0]["minimum_history"] == 2


def test_build_metric_artifacts_supports_global_entity_for_cross_cartridge_metrics():
    metric = _metric()
    metric["id"] = "capacity_gap"
    metric["dataset"] = "salesforce_forecast_vs_capacidad"
    metric["entity"] = {"kind": "pipeline", "id_field": "__all__", "label_field": "Forecast vs capacidad"}
    metric["value_field"] = "holgura_horas"
    rows = [
        {"mes": "2026-01-01", "holgura_horas": 100},
        {"mes": "2026-02-01", "holgura_horas": 100},
        {"mes": "2026-03-01", "holgura_horas": 40},
    ]

    artifacts, _ = intelligence_engine.build_metric_artifacts({"cartridge": "salesforce", "domain": "Ventas"}, metric, rows)

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
            {"mes": "2026-01-01", "owner_id": "u1", "vendedor": "Sofia", "forecast_ponderado_usd": 100},
            {"mes": "2026-02-01", "owner_id": "u1", "vendedor": "Sofia", "forecast_ponderado_usd": 120},
            {"mes": "2026-03-01", "owner_id": "u1", "vendedor": "Sofia", "forecast_ponderado_usd": 200},
        ]

    monkeypatch.setattr(
        intelligence_engine,
        "load_contracts",
        lambda cartridge_ids=None: [{**_contract(), "metrics": [_metric()]}],
    )

    result = await intelligence_engine.run_intelligence(USER, {}, fetcher=fake_fetcher, persist=False)

    assert result["workspace_id"] == USER["active_workspace_id"]
    assert len(result["signals"]) == 1
    assert result["signals"][0]["signal_id"].startswith("intel:")
