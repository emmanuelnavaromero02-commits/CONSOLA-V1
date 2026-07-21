from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
import pytest

from app.services import control_room_service, intelligence_engine
from app.services.intelligence import persistence as intelligence_persistence


TENANT_A = "11111111-1111-1111-1111-111111111111"
WORKSPACE_A = "22222222-2222-2222-2222-222222222222"
TENANT_B = "33333333-3333-3333-3333-333333333333"
WORKSPACE_B = "44444444-4444-4444-4444-444444444444"

REPLICON_USER = {
    "id": 7,
    "email": "ops@example.com",
    "active_tenant_id": TENANT_A,
    "active_workspace_id": WORKSPACE_A,
    "allowed_cartridges": ["replicon"],
    "role": "admin",
}


class _AsyncContext:
    def __init__(self, value=None):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *_args):
        return None


class _ScopedConnection:
    def __init__(
        self,
        *,
        fetch_return=None,
        fetch_side_effect=None,
        fetchrow_return=None,
        fetchrow_side_effect=None,
    ):
        self.execute = AsyncMock()
        self.fetch = AsyncMock(return_value=fetch_return)
        if fetch_side_effect is not None:
            self.fetch.side_effect = fetch_side_effect
        self.fetchrow = AsyncMock(return_value=fetchrow_return)
        if fetchrow_side_effect is not None:
            self.fetchrow.side_effect = fetchrow_side_effect

    def transaction(self):
        return _AsyncContext()


class _ScopedPool:
    def __init__(self, connection: _ScopedConnection):
        self.connection = connection

    def acquire(self):
        return _AsyncContext(self.connection)


def _assert_scope_call(
    conn: _ScopedConnection,
    *,
    tenant_id: str = TENANT_A,
    workspace_id: str = WORKSPACE_A,
    call_index: int = 0,
):
    args = conn.execute.await_args_list[call_index].args
    assert "set_config('app.tenant_id'" in args[0]
    assert "set_config('app.workspace_id'" in args[0]
    assert args[1:] == (tenant_id, workspace_id)


async def replicon_gold_fetcher(
    dataset: str, user: dict | None, limit: int
) -> list[dict]:
    assert user == REPLICON_USER
    assert limit >= 3
    if dataset != "consultor_mensual":
        return []
    return [
        {
            "tenant_id": TENANT_A,
            "workspace_id": WORKSPACE_A,
            "mes": "2026-03-01",
            "consultor": "Andrea Morales",
            "horas_facturables": 122,
        },
        {
            "tenant_id": TENANT_A,
            "workspace_id": WORKSPACE_A,
            "mes": "2026-04-01",
            "consultor": "Andrea Morales",
            "horas_facturables": 124,
        },
        {
            "tenant_id": TENANT_A,
            "workspace_id": WORKSPACE_A,
            "mes": "2026-05-01",
            "consultor": "Andrea Morales",
            "horas_facturables": 130,
        },
        {
            "tenant_id": TENANT_A,
            "workspace_id": WORKSPACE_A,
            "mes": "2026-06-01",
            "consultor": "Andrea Morales",
            "horas_facturables": 40,
        },
    ]


@pytest.mark.asyncio
async def test_replicon_gold_generates_scoped_intelligence_signal_with_evidence():
    result = await intelligence_engine.run_intelligence(
        REPLICON_USER,
        {"cartridge_id": "replicon", "metrics": ["billable_hours"], "horizon_days": []},
        fetcher=replicon_gold_fetcher,
        persist=False,
    )

    assert result["skipped"] == []
    assert result["signals"]
    artifact = next(
        item
        for item in result["artifacts"]
        if item["signal"]["signal_subtype"] == "observed"
    )
    signal = artifact["signal"]
    evidence = artifact["evidence_pack"]
    decision = artifact["decision_intelligence"]
    item = evidence["items"][0]

    assert signal["cartridge_id"] == "replicon"
    assert signal["source_system"] == "replicon"
    assert signal["dataset"] == "consultor_mensual"
    assert signal["source_dataset"] == "consultor_mensual"
    assert signal["gold_table"] == "gold_consultor_mensual"
    assert signal["freshness_at"] == "2026-06-01"
    assert signal["freshness_field"] == "mes"
    assert signal["severity"] in {"critical", "high"}
    assert signal["decision_intelligence"] == decision
    assert decision["method"] == "robust_residual_v0"
    assert decision["time_series"]["method"] == "robust_residual_v0"
    assert (
        decision["time_series"]["seasonality"]["status"] == "insufficient_seasonality"
    )
    assert decision["time_series"]["residual"]["robust_z"] is not None
    assert decision["expected_impact"]["currency"] == "USD"
    assert decision["expected_impact"]["value"] > 0
    assert decision["recommended_decision"] in {"investigate", "act_now"}
    assert decision["data_quality"]["status"] == "sufficient"
    assert item["source_ref"] == "consultor_mensual"
    assert item["query_text"].startswith(
        "SELECT mes, consultor, horas_facturables FROM gold_consultor_mensual"
    )
    assert item["data"]["row_count"] == 4
    assert item["data"]["sample_hash"]
    assert item["data"]["source_system"] == "replicon"
    assert item["data"]["gold_table"] == "gold_consultor_mensual"
    assert item["metadata"]["freshness_at"] == "2026-06-01"
    assert evidence["source_system"] == "replicon"
    assert evidence["gold_table"] == "gold_consultor_mensual"


@pytest.mark.asyncio
async def test_missing_gold_dataset_is_reported_without_inventing_signals():
    async def missing_gold(dataset: str, _user: dict | None, _limit: int) -> list[dict]:
        raise HTTPException(404, f"dataset unavailable: {dataset}")

    result = await intelligence_engine.run_intelligence(
        REPLICON_USER,
        {"cartridge_id": "replicon", "metrics": ["billable_hours"]},
        fetcher=missing_gold,
        persist=False,
    )

    assert result["signals"] == []
    assert result["artifacts"] == []
    assert result["skipped"]
    assert all(item["status"] == "dataset_unavailable" for item in result["skipped"])
    assert all("decision_intelligence" not in item for item in result["skipped"])
    assert {
        "cartridge_id": "replicon",
        "dataset": "consultor_mensual",
        "metric": "billable_hours",
        "status": "dataset_unavailable",
        "reason": "dataset unavailable: consultor_mensual",
    } in result["skipped"]


@pytest.mark.asyncio
async def test_publish_control_room_item_persists_decision_intelligence_impact():
    decision = {
        "method": "robust_residual_v0",
        "anomaly_probability": 0.88,
        "probability_basis": "coarse rarity score from residual robust_z; not calibrated Bayesian posterior",
        "uncertainty_level": "medium",
        "confidence_interval": {
            "lower": 121.03,
            "upper": 126.97,
            "unit": "horas_facturables",
        },
        "expected_impact": {
            "value": 10440,
            "currency": "USD",
            "basis": "abs(deviation_value) * impact.unit_value (120)",
        },
        "cost_of_delay": {
            "value_per_day": 348,
            "currency": "USD",
            "basis": "expected_impact / 30-day operating month.",
        },
        "downside_risk": {
            "value": 13050,
            "currency": "USD",
            "basis": "expected_impact * 1.25 uncertainty multiplier.",
        },
        "value_of_information": {
            "level": "medium",
            "rationale": "Additional evidence may change timing or owner of the action.",
        },
        "recommended_decision": "investigate",
        "recommended_next_step": "Review evidence before execution.",
        "rationale": "Decision Intelligence v0 used robust baseline evidence.",
        "options": [
            {
                "option": "act_now",
                "expected_utility": 7000,
                "utility_basis": "proxy",
                "risk": "medium",
                "explanation": "Dry-run first.",
            },
            {
                "option": "investigate",
                "expected_utility": 1100,
                "utility_basis": "proxy",
                "risk": "medium",
                "explanation": "Review evidence.",
            },
            {
                "option": "wait",
                "expected_utility": -2400,
                "utility_basis": "proxy",
                "risk": "medium",
                "explanation": "Wait one refresh.",
            },
            {
                "option": "monitor",
                "expected_utility": -900,
                "utility_basis": "proxy",
                "risk": "low",
                "explanation": "Watch only.",
            },
        ],
        "data_quality": {
            "history_points": 3,
            "minimum_required": 3,
            "status": "sufficient",
            "missing_fields": [],
        },
        "time_series": {
            "method": "robust_residual_v0",
            "history_points": 3,
            "periods_observed": 4,
            "time_field": "mes",
            "value_field": "horas_facturables",
            "entity_key": "Andrea Morales",
            "trend": {
                "method": "theil_sen_v0",
                "current_value": 134,
                "slope_per_period": 4,
                "basis": "test trend",
            },
            "seasonality": {
                "method": "none",
                "period": "month_of_year",
                "component": None,
                "status": "insufficient_seasonality",
                "basis": "test seasonality",
            },
            "residual": {
                "value": -94,
                "median": 0,
                "mad": 1,
                "robust_z": 94,
                "basis": "test residual",
            },
        },
    }
    artifact = {
        "signal": {
            "signal_id": "intel:replicon-gold",
            "cartridge_id": "replicon",
            "domain": "Rentabilidad",
            "dataset": "consultor_mensual",
            "source_dataset": "consultor_mensual",
            "gold_table": "gold_consultor_mensual",
            "freshness_at": "2026-06-01",
            "freshness_field": "mes",
            "summary": "Horas facturables mensuales por consultor: Andrea Morales bajo baseline.",
            "severity": "critical",
            "entity_kind": "consultant",
            "entity_id": "Andrea Morales",
            "entity_label": "Andrea Morales",
            "metric": "billable_hours",
            "metric_name": "Horas facturables mensuales por consultor",
            "actual_value": 40,
            "expected_value": 127,
            "deviation_value": -87,
            "deviation_pct": -0.685,
            "confidence": 0.85,
            "decision_intelligence": decision,
        },
        "evidence_pack": {
            "id": 42,
            "summary": "Baseline robusto.",
            "confidence": 0.85,
            "items": [
                {
                    "query_text": "SELECT mes, consultor, horas_facturables FROM gold_consultor_mensual",
                    "source_ref": "consultor_mensual",
                    "data": {"row_count": 4},
                }
            ],
        },
        "hypotheses": [{"title": "Cambio de capacidad"}],
        "options": [{"label": "Investigar", "option_id": "investigate"}],
        "decision_intelligence": decision,
        "control_origin": "intelligence_signal",
        "math_provenance": {
            "ruleset_version": "control_room_gold_signal.v1",
            "control_origin": "intelligence_signal",
            "formula": "baseline -> deviation -> robust residual/MAD -> probability",
            "input_hash": "abc123",
            "bayesian_calibration": {
                "status": "not_calibrated",
                "reason": "missing_calibration_state",
                "sample_count": 0,
                "raw_probability": 0.88,
            },
        },
        "priority": {
            "score": 91,
            "formula": "severity + confidence + deviation",
            "drivers": {"severity": 26, "confidence": 20, "deviation": 24},
        },
        "monte_carlo": {
            "status": "completed",
            "mode": "derived_mode",
            "seed": 123,
            "reproducibility_hash": "mc-hash",
        },
        "bayesian_calibration": {
            "status": "not_calibrated",
            "reason": "missing_calibration_state",
            "sample_count": 0,
            "raw_probability": 0.88,
        },
    }
    mock_pool = AsyncMock()
    mock_pool.execute = AsyncMock()

    await intelligence_persistence.publish_control_room_item(
        mock_pool,
        TENANT_A,
        WORKSPACE_A,
        REPLICON_USER,
        artifact,
    )

    first_call = mock_pool.execute.await_args_list[0]
    args = first_call.args
    metadata = json.loads(args[15])
    assert metadata["decision_intelligence"] == decision
    assert metadata["intelligence"]["decision_intelligence"] == decision
    assert metadata["control_origin"] == "intelligence_signal"
    assert (
        metadata["math_provenance"]["ruleset_version"] == "control_room_gold_signal.v1"
    )
    assert metadata["priority"]["score"] == 91
    assert metadata["priority"]["drivers"]["severity"] == 26
    assert metadata["monte_carlo"]["mode"] == "derived_mode"
    assert metadata["bayesian_calibration"]["status"] == "not_calibrated"
    assert metadata["details"]["bayesian_calibration_status"] == "not_calibrated"
    assert metadata["details"]["decision_intelligence_method"] == "robust_residual_v0"
    assert metadata["details"]["time_series_method"] == "robust_residual_v0"
    assert metadata["details"]["residual_z"] == 94
    assert metadata["details"]["seasonality_status"] == "insufficient_seasonality"
    assert metadata["details"]["recommended_decision"] == "investigate"
    assert args[16] == 10440
    assert args[17] == "USD"
    assert args[19] == 91


@pytest.mark.asyncio
async def test_control_room_lists_persisted_gold_signal_with_source_evidence_and_scope():
    metadata = {
        "tenant_id": TENANT_A,
        "workspace_id": WORKSPACE_A,
        "source_system": "replicon",
        "source_dataset": "consultor_mensual",
        "dataset": "consultor_mensual",
        "gold_table": "gold_consultor_mensual",
        "freshness_at": "2026-06-01",
        "freshness_field": "mes",
        "data_status": "gold_ready",
        "metric_type": "scalar",
        "observation_date": "2026-06-01",
        "evidence_pack_id": 42,
        "evidence_pack": {
            "id": 42,
            "summary": "Baseline moving_average con 2 muestras historicas.",
            "confidence": 0.85,
            "items": [
                {
                    "source_type": "dataset",
                    "source_ref": "consultor_mensual",
                    "data": {"row_count": 3, "sample_hash": "abc123"},
                }
            ],
        },
        "module": "Intelligence Engine",
        "description": "Horas facturables mensuales por consultor: Andrea Morales bajo baseline.",
        "recommendation": "Pedir seguimiento al manager",
        "root_cause": "Cambio de capacidad o asignacion",
        "impact": "Desviacion -87.00 en Horas facturables mensuales por consultor.",
        "details": {
            "actual_value": 40,
            "expected_value": 127,
            "deviation_pct": -0.685,
            "evidence_pack_id": 42,
            "source_system": "replicon",
            "source_dataset": "consultor_mensual",
            "gold_table": "gold_consultor_mensual",
            "freshness_at": "2026-06-01",
            "freshness_field": "mes",
        },
        "sql": "SELECT mes, consultor, horas_facturables FROM gold_consultor_mensual WHERE consultor = $entity_id ORDER BY mes",
        "decision_intelligence": {
            "method": "robust_baseline_v0",
            "anomaly_probability": 0.95,
            "probability_basis": "median=124; mad=2; history_points=3",
            "uncertainty_level": "medium",
            "confidence_interval": {
                "lower": 121.03,
                "upper": 126.97,
                "unit": "horas_facturables",
            },
            "expected_impact": {
                "value": 10440,
                "currency": "USD",
                "basis": "abs(deviation_value) * impact.unit_value (120)",
            },
            "cost_of_delay": {
                "value_per_day": 348,
                "currency": "USD",
                "basis": "expected_impact / 30-day operating month.",
            },
            "downside_risk": {
                "value": 13050,
                "currency": "USD",
                "basis": "expected_impact * 1.25 uncertainty multiplier.",
            },
            "value_of_information": {
                "level": "medium",
                "rationale": "Additional evidence may change timing or owner of the action.",
            },
            "recommended_decision": "investigate",
            "recommended_next_step": "Review evidence pack before execution.",
            "rationale": "Decision Intelligence v0 used a robust median/MAD baseline.",
            "options": [
                {
                    "option": "act_now",
                    "expected_utility": 8000,
                    "utility_basis": "proxy",
                    "risk": "medium",
                    "explanation": "Dry-run first.",
                },
                {
                    "option": "investigate",
                    "expected_utility": 1200,
                    "utility_basis": "proxy",
                    "risk": "medium",
                    "explanation": "Review evidence.",
                },
                {
                    "option": "wait",
                    "expected_utility": -2500,
                    "utility_basis": "proxy",
                    "risk": "medium",
                    "explanation": "Wait one refresh.",
                },
                {
                    "option": "monitor",
                    "expected_utility": -1000,
                    "utility_basis": "proxy",
                    "risk": "low",
                    "explanation": "Watch only.",
                },
            ],
            "data_quality": {
                "history_points": 3,
                "minimum_required": 3,
                "status": "sufficient",
                "missing_fields": [],
            },
        },
        "bayesian_calibration": {
            "status": "not_calibrated",
            "reason": "missing_calibration_state",
            "sample_count": 0,
            "raw_probability": 0.95,
        },
        "intelligence": {
            "signal": {"signal_id": "intel:replicon-gold"},
            "decision_intelligence": {
                "method": "robust_baseline_v0",
                "recommended_decision": "investigate",
            },
        },
    }
    conn = _ScopedConnection(
        fetch_return=[
            {
                "tenant_id": TENANT_A,
                "workspace_id": WORKSPACE_A,
                "item_id": "intel:replicon-gold",
                "cartridge_id": "replicon",
                "domain": "Rentabilidad",
                "source_dataset": "consultor_mensual",
                "item_kind": "intelligence_signal",
                "title": "Horas facturables mensuales por consultor",
                "severity": "critical",
                "status": "open",
                "decision_id": None,
                "entity_kind": "consultant",
                "entity_id": "Andrea Morales",
                "entity_label": "Andrea Morales",
                "anomaly_type": "billable_hours",
                "metadata": metadata,
                "first_seen_at": datetime(2026, 6, 12, tzinfo=UTC),
                "last_seen_at": datetime(2026, 6, 12, tzinfo=UTC),
                "resolved_at": None,
                "dismissed_at": None,
                "impact_estimate": 87,
                "impact_currency": "USD",
                "confidence": 0.85,
                "priority_score": 90,
                "selected_option_id": None,
                "execution_status": "not_started",
            }
        ]
    )
    mock_pool = _ScopedPool(conn)

    with patch.object(control_room_service.auth, "pool", return_value=mock_pool):
        items = await control_room_service._persisted_intelligence_items(REPLICON_USER)

    _assert_scope_call(conn)
    sql, workspace_arg, kinds, tenant_arg, page_size = conn.fetch.await_args.args
    assert "tenant_id::text" in sql
    assert workspace_arg == WORKSPACE_A
    assert kinds == ["intelligence_signal", "agent_alert"]
    assert tenant_arg == TENANT_A
    assert page_size == 200
    assert len(items) == 1
    item = items[0]
    assert item["tenant_id"] == TENANT_A
    assert item["workspace_id"] == WORKSPACE_A
    assert item["source_system"] == "replicon"
    assert item["dataset"] == "consultor_mensual"
    assert item["gold_table"] == "gold_consultor_mensual"
    assert item["freshness_at"] == "2026-06-01"
    assert item["evidence_pack_id"] == 42
    assert item["evidence_pack"]["items"][0]["source_ref"] == "consultor_mensual"
    assert item["details"]["source_dataset"] == "consultor_mensual"
    assert item["sql"].startswith(
        "SELECT mes, consultor, horas_facturables FROM gold_consultor_mensual"
    )
    decision = item["decision_intelligence"]
    assert decision["method"] == "robust_baseline_v0"
    assert decision["recommended_decision"] == "investigate"
    assert item["intelligence"]["decision_intelligence"] == decision
    assert item["omega"]["decision_intelligence"] == decision
    assert item["omega"]["intelligence"]["decision_intelligence"] == decision
    assert item["bayesian_calibration"]["status"] == "not_calibrated"


@pytest.mark.asyncio
async def test_control_room_persisted_signal_read_is_scoped_by_tenant_and_workspace():
    async def scoped_fetch(
        query: str,
        workspace_id: str,
        kinds: list[str],
        tenant_id: str,
        _page_size: int,
    ):
        assert "workspace_id = $1" in query
        assert "item_kind = ANY($2::text[])" in query
        assert "tenant_id::text = $3" in query
        assert kinds == ["intelligence_signal", "agent_alert"]
        if workspace_id == WORKSPACE_A and tenant_id == TENANT_A:
            return [
                {
                    "item_id": "intel:a",
                    "metadata": {
                        "data_status": "ready",
                        "metric_type": "scalar",
                        "observed_value": 1,
                        "observation_date": "2026-07-16",
                        "evidence_refs": ["gold_metrics:intel:a"],
                    },
                    "item_kind": "intelligence_signal",
                    "source_dataset": "gold_metrics",
                }
            ]
        return []

    conn = _ScopedConnection(fetch_side_effect=scoped_fetch)
    mock_pool = _ScopedPool(conn)
    user_b = {
        **REPLICON_USER,
        "active_tenant_id": TENANT_B,
        "active_workspace_id": WORKSPACE_B,
    }

    with patch.object(control_room_service.auth, "pool", return_value=mock_pool):
        a_items = await control_room_service._persisted_intelligence_items(
            REPLICON_USER
        )
        b_items = await control_room_service._persisted_intelligence_items(user_b)

    assert [item["id"] for item in a_items] == ["intel:a"]
    assert b_items == []
    _assert_scope_call(conn)
    _assert_scope_call(
        conn,
        tenant_id=TENANT_B,
        workspace_id=WORKSPACE_B,
        call_index=1,
    )
    assert conn.fetch.await_args_list[0].args[1:] == (
        WORKSPACE_A,
        ["intelligence_signal", "agent_alert"],
        TENANT_A,
        200,
    )
    assert conn.fetch.await_args_list[1].args[1:] == (
        WORKSPACE_B,
        ["intelligence_signal", "agent_alert"],
        TENANT_B,
        200,
    )


@pytest.mark.asyncio
async def test_control_room_persisted_signal_read_is_owner_scoped_for_non_admin():
    async def scoped_fetch(
        query: str,
        workspace_id: str,
        kinds: list[str],
        tenant_id: str,
        owner_id: int,
        _page_size: int,
    ):
        assert "workspace_id = $1" in query
        assert "item_kind = ANY($2::text[])" in query
        assert "tenant_id::text = $3" in query
        assert "owner_user_id = $4" in query
        assert kinds == ["intelligence_signal", "agent_alert"]
        assert workspace_id == WORKSPACE_A
        assert tenant_id == TENANT_A
        assert owner_id == 11
        return [
            {
                "item_id": "intel:owned",
                "metadata": {
                    "data_status": "ready",
                    "metric_type": "scalar",
                    "observed_value": 1,
                    "observation_date": "2026-07-16",
                    "evidence_refs": ["gold_metrics:intel:owned"],
                },
                "item_kind": "intelligence_signal",
                "source_dataset": "gold_metrics",
            }
        ]

    conn = _ScopedConnection(fetch_side_effect=scoped_fetch)
    mock_pool = _ScopedPool(conn)
    employee_user = {
        **REPLICON_USER,
        "id": 11,
        "role": "analyst",
        "workspace_role": "member",
    }

    with patch.object(control_room_service.auth, "pool", return_value=mock_pool):
        items = await control_room_service._persisted_intelligence_items(employee_user)

    assert [item["id"] for item in items] == ["intel:owned"]
    _assert_scope_call(conn)
    assert conn.fetch.await_args.args[1:] == (
        WORKSPACE_A,
        ["intelligence_signal", "agent_alert"],
        TENANT_A,
        11,
        200,
    )


@pytest.mark.asyncio
async def test_control_room_persisted_item_for_mutation_is_owner_scoped_for_non_admin():
    async def scoped_fetchrow(
        query: str, workspace_id: str, item_id: str, tenant_id: str
    ):
        assert "workspace_id = $1" in query
        assert "item_id = $2" in query
        assert "tenant_id::text = $3" in query
        assert "owner_user_id =" not in query
        assert workspace_id == WORKSPACE_A
        assert item_id == "intel:other"
        assert tenant_id == TENANT_A
        return None

    conn = _ScopedConnection(fetchrow_side_effect=scoped_fetchrow)
    mock_pool = _ScopedPool(conn)
    employee_user = {
        **REPLICON_USER,
        "id": 11,
        "role": "analyst",
        "workspace_role": "member",
    }

    with patch.object(control_room_service.auth, "pool", return_value=mock_pool):
        item = await control_room_service._persisted_item_for_mutation(
            "intel:other", employee_user
        )

    assert item is None
    _assert_scope_call(conn)
    assert conn.fetchrow.await_args.args[1:] == (
        WORKSPACE_A,
        "intel:other",
        TENANT_A,
    )


@pytest.mark.asyncio
async def test_persisted_derived_item_keeps_state_until_parent_validation():
    row = {
        "tenant_id": TENANT_A,
        "workspace_id": WORKSPACE_A,
        "item_id": "derived-1",
        "cartridge_id": "replicon",
        "domain": "Operacion",
        "source_dataset": "gold_workforce",
        "item_kind": "agent_alert",
        "title": "Derived alert",
        "severity": "high",
        "status": "in_review",
        "decision_id": 42,
        "entity_kind": "employee",
        "entity_id": "7",
        "entity_label": "Employee 7",
        "anomaly_type": "capacity_risk",
        "metadata": {"parent_item_id": "parent-1", "data_status": "ready"},
        "first_seen_at": None,
        "last_seen_at": None,
        "resolved_at": None,
        "dismissed_at": None,
        "impact_estimate": 12,
        "impact_currency": "USD",
        "confidence": 0.9,
        "priority_score": 88,
        "selected_option_id": "review",
        "execution_status": "dry_run_validated",
    }
    conn = _ScopedConnection(fetchrow_return=row)

    with patch.object(
        control_room_service.auth,
        "pool",
        return_value=_ScopedPool(conn),
    ):
        item = await control_room_service._persisted_item_for_mutation(
            "derived-1", REPLICON_USER
        )

    assert item["decision_id"] == 42
    assert item["selected_option_id"] == "review"
    assert item["execution_status"] == "dry_run_validated"
    assert item["priority_score"] == 88
