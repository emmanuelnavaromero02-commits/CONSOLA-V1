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
        user, *, request, source_system, run_mode, datasets_evaluated
    ):
        assert request == {}
        assert source_system is None
        assert run_mode == "manual"
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
        user, *, request, source_system, run_mode, datasets_evaluated
    ):
        assert request["run_mode"] == "scheduled"
        assert source_system == "hubspot"
        assert run_mode == "scheduled"
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
