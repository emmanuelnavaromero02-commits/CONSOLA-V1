from __future__ import annotations

from copy import deepcopy

import pytest

from app.services.intelligence import gold_fetcher
from app.services.control_room.business_impact_rules import calculate_item_impact
from app.services.control_room import api as control_room_api


READINESS = "sap_successfactors_talent_readiness"
NINE_BOX = "sap_successfactors_talent_9box"
SIMULATION_INPUTS = "sap_successfactors_talent_simulation_inputs"
BENCHMARK = "sap_successfactors_talent_benchmark_internal"


def _project(dataset: str, row: dict) -> dict:
    return gold_fetcher._project_operational_truth_rows(dataset, [row])[0]


def test_historical_readiness_without_durable_approval_fails_closed() -> None:
    historical = {
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "employee-a",
        "source_mode": "benchmark_internal",
        "readiness_status": "ready",
        "benchmark_raw_score": 71.0,
        "benchmark_score": 88.0,
        "readiness_score": 88.0,
        "confidence": 0.6,
        "blocker_count": 4,
        "contract_version": "talent_readiness.v2",
    }

    projected = _project(READINESS, historical)

    assert set(projected) == set(historical)
    assert projected["source_mode"] == "insufficient_data"
    assert projected["readiness_status"] == "insufficient_data"
    assert projected["benchmark_raw_score"] is None
    assert projected["benchmark_score"] is None
    assert projected["readiness_score"] is None
    assert projected["confidence"] is None
    assert projected["blocker_count"] is None
    assert historical["readiness_score"] == 88.0


def test_durable_readiness_remains_usable_and_unchanged() -> None:
    durable = {
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "source_mode": "benchmark_internal",
        "readiness_status": "ready",
        "benchmark_approval_valid": True,
        "benchmark_provenance_status": "approved_durable",
        "benchmark_raw_score": 71.0,
        "benchmark_score": 88.0,
        "readiness_score": 88.0,
        "confidence": 0.6,
    }
    expected = deepcopy(durable)

    assert _project(READINESS, durable) == expected


def test_historical_nine_box_cannot_publish_classification() -> None:
    historical = {
        "source_mode": "benchmark_internal",
        "box_status": "benchmark_internal",
        "benchmark_approval_valid": False,
        "benchmark_provenance_status": "stale_unapproved_benchmark",
        "benchmark_performance_proxy": 4.2,
        "benchmark_potential_proxy": 3.8,
        "performance_band": "high",
        "potential_band": "high",
        "box_key": "high_high",
    }

    projected = _project(NINE_BOX, historical)

    assert set(projected) == set(historical)
    assert projected["source_mode"] == "insufficient_data"
    assert projected["box_status"] == "blocked"
    assert projected["benchmark_performance_proxy"] is None
    assert projected["benchmark_potential_proxy"] is None
    assert projected["performance_band"] is None
    assert projected["potential_band"] is None
    assert projected["box_key"] is None


def test_historical_simulation_inputs_cannot_start_analysis() -> None:
    historical = {
        "source_mode": "benchmark_internal",
        "readiness_status": "benchmark_internal",
        "input_status": "ready",
        "scenario_count": 3,
        "input_variables_json": '{"expected_delta":{"type":"fixed","value":10}}',
        "escenarios": '[{"name":"improved","risk":15}]',
        "confidence": 0.6,
        "blocked_reason": None,
    }

    projected = _project(SIMULATION_INPUTS, historical)

    assert set(projected) == set(historical)
    assert projected["source_mode"] == "insufficient_data"
    assert projected["readiness_status"] == "insufficient_data"
    assert projected["input_status"] == "blocked"
    assert projected["scenario_count"] == 0
    assert projected["input_variables_json"] is None
    assert projected["escenarios"] is None
    assert projected["confidence"] is None
    assert projected["blocked_reason"] == "stale_unapproved_benchmark"


def test_real_cpa_row_preserves_observed_result_but_clears_benchmark_residue() -> None:
    observed = {
        "source_mode": "cpa_real",
        "readiness_status": "ready",
        "fit_score": 91.0,
        "readiness_score": 91.0,
        "confidence": 0.85,
        "benchmark_approval_valid": False,
        "benchmark_provenance_status": "stale_unapproved_benchmark",
        "benchmark_raw_score": 71.0,
        "benchmark_score": 88.0,
    }

    projected = _project(READINESS, observed)

    assert set(projected) == set(observed)
    assert projected["source_mode"] == "cpa_real"
    assert projected["readiness_status"] == "ready"
    assert projected["fit_score"] == 91.0
    assert projected["readiness_score"] == 91.0
    assert projected["confidence"] == 0.85
    assert projected["benchmark_raw_score"] is None
    assert projected["benchmark_score"] is None


def test_benchmark_row_requires_complete_server_attestation() -> None:
    invalid = {
        "approved": True,
        "approved_by": "42",
        "approved_at": "2026-07-30T10:00:00Z",
        "approval_actor_source": "server",
        "approval_recorded_by_server": True,
        "approval_authorization_verified": False,
        "approval_evidence_ref": "evidence-1",
        "approval_authorization_ref": "authorization-1",
        "approval_valid": True,
        "approval_status": "approved",
        "approval_source": "historical_seed",
        "benchmark_version": "talent_benchmark.approved.v1",
    }

    projected = _project(BENCHMARK, invalid)

    assert set(projected) == set(invalid)
    assert projected["approved"] is False
    assert projected["approval_valid"] is False
    assert projected["approval_status"] == "unreviewed"
    assert projected["benchmark_version"] is None
    assert projected["approval_source"] is None
    assert projected["approved_by"] is None
    assert projected["approved_at"] is None
    assert projected["approval_evidence_ref"] is None
    assert projected["approval_authorization_ref"] is None


def test_benchmark_row_accepts_complete_verified_server_attestation() -> None:
    valid = {
        "approved": True,
        "approved_by": "42",
        "approved_at": "2026-07-30T10:00:00Z",
        "approval_actor_source": "server",
        "approval_recorded_by_server": True,
        "approval_authorization_verified": True,
        "approval_evidence_ref": "evidence-1",
        "approval_authorization_ref": "authorization-1",
        "approval_valid": True,
    }

    assert _project(BENCHMARK, valid) == valid


@pytest.mark.parametrize(
    "actor",
    ["bot:approver", "svc:talent", "ci:benchmark", "Alice", "0", "+1", "01", -1, True],
)
def test_benchmark_rejects_non_server_actor_identifiers(actor) -> None:
    invalid = {
        "approved": True,
        "approved_by": actor,
        "approved_at": "2026-07-30T10:00:00Z",
        "approval_actor_source": "server",
        "approval_recorded_by_server": True,
        "approval_authorization_verified": True,
        "approval_evidence_ref": "evidence-1",
        "approval_authorization_ref": "authorization-1",
        "approval_valid": True,
        "approval_status": "approved",
    }

    projected = _project(BENCHMARK, invalid)

    assert projected["approved"] is False
    assert projected["approved_by"] is None
    assert projected["approval_valid"] is False
    assert projected["approval_status"] == "unreviewed"


def test_replicon_financial_alert_requires_ready_currency_provenance() -> None:
    def number(value):
        return float(value) if value is not None else None

    def payload(**values):
        return values

    item = {
        "cartridge": "replicon",
        "anomaly_type": "low_margin",
        "impact_estimate": 9000,
        "details": {"revenue_usd": 10000, "margen_bruto_usd": 100},
    }
    blocked = calculate_item_impact(item, number=number, payload=payload)
    item["details"].update(
        financial_status="ready",
        base_currency="USD",
        original_currency="USD",
    )
    ready = calculate_item_impact(item, number=number, payload=payload)

    assert blocked["status"] == "unavailable"
    assert blocked["estimate"] is None
    assert ready["status"] == "ok"
    assert ready["estimate"] == 9000


def test_replicon_wip_impact_does_not_treat_missing_wip_as_zero() -> None:
    item = {
        "cartridge": "replicon",
        "anomaly_type": "wip_variance",
        "details": {
            "financial_status": "ready",
            "base_currency": "USD",
            "original_currency": "USD",
            "revenue_usd": 10000,
            "margen_bruto_usd": 100,
            "wip_usd": None,
        },
    }
    result = calculate_item_impact(
        item,
        number=lambda value: float(value) if value is not None else None,
        payload=lambda **values: values,
    )

    assert result["status"] == "unavailable"
    assert result["estimate"] is None


def test_financial_summary_does_not_publish_null_fx_as_zero() -> None:
    sources = [
        {
            "dataset": "pnl_mensual",
            "status": "ok",
            "count": 1,
            "domain": "financial",
            "cartridge": "replicon",
        }
    ]
    rows = {
        "pnl_mensual": [
            {
                "financial_status": "missing_fx",
                "revenue_usd": None,
                "facturacion_mes_usd": None,
                "wip_usd": None,
                "costo_total": None,
                "margen_bruto_usd": None,
            }
        ]
    }
    metrics = control_room_api._financial_metrics(sources, rows)

    assert metrics["status"] == "missing_fx"
    for field in (
        "revenue_usd",
        "billed_usd",
        "wip_usd",
        "cost_usd",
        "margin_usd",
        "margin_pct",
    ):
        assert metrics[field] is None
    assert metrics["risk_projects"] == []

    unproven = dict(rows["pnl_mensual"][0])
    unproven.update(
        financial_status="ready",
        revenue_usd=100,
        facturacion_mes_usd=50,
        wip_usd=50,
        costo_total=25,
        margen_bruto_usd=75,
    )
    metrics = control_room_api._financial_metrics(
        sources,
        {"pnl_mensual": [unproven]},
    )
    assert metrics["status"] == "insufficient_data"
    assert metrics["revenue_usd"] is None
    assert metrics["risk_projects"] == []

    unknown = dict(rows["pnl_mensual"][0], financial_status="unavailable")
    metrics = control_room_api._financial_metrics(
        sources,
        {"pnl_mensual": [unknown]},
    )
    assert metrics["status"] == "unavailable"
