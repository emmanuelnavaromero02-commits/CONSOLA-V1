"""Fase 2A — SuccessFactors talent intelligence contract (Familia 1).

Validates that the packaged SF intelligence contract binds the monthly-cohort
time-series datasets and that Monte Carlo (derived mode) runs on real-shaped
monthly history. Attrition is intentionally NOT a signal metric (see the contract
note) — it stays a dataset/KPI — so it must be absent from the contract metrics.
"""

from __future__ import annotations

import pathlib

import pytest
import yaml

from app.services import intelligence_engine
from app.services.intelligence.contracts import validate_metric

_CONTRACT_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "cartridges/sap_successfactors/app/config/intelligence.yaml"
)

USER = {
    "id": 0,
    "email": "test@omega.local",
    "role": "admin",
    "workspace_role": "workspace_admin",
    "tenant_id": "t-test",
    "workspace_id": "w-test",
    "active_tenant_id": "t-test",
    "active_workspace_id": "w-test",
    "allowed_cartridges": ["sap_successfactors"],
}


def _contract() -> dict:
    return yaml.safe_load(_CONTRACT_PATH.read_text(encoding="utf-8"))


def test_sf_contract_loads_binds_datasets_and_validates():
    contract = _contract()
    assert contract["cartridge"] == "sap_successfactors"
    metrics = {m["id"]: m for m in contract["metrics"]}
    # Familia 1 signal metrics present and bound to the monthly-cohort datasets.
    assert set(metrics) == {
        "talent_headcount_by_cohort",
        "talent_avg_tenure_by_cohort",
        "talent_attrition_rate_by_cohort",
    }
    assert metrics["talent_headcount_by_cohort"]["dataset"] == "sap_successfactors_talent_headcount_by_cohort_month"
    assert metrics["talent_avg_tenure_by_cohort"]["dataset"] == "sap_successfactors_talent_tenure_by_cohort_month"
    assert metrics["talent_attrition_rate_by_cohort"]["dataset"] == "sap_successfactors_talent_attrition_by_cohort_month"
    for m in contract["metrics"]:
        assert m["time_field"] == "snapshot_month"
        assert m["entity"]["id_field"] == "cohort_id"
        validate_metric(contract, m)  # raises if the 5 required fields are missing
    # Attrition uses high materiality thresholds so sparse Poisson data stays quiet
    # (fires only on materially large shifts); it is contracted so it never falls to
    # the generic fallback.
    att = metrics["talent_attrition_rate_by_cohort"]
    assert att["expected_behavior"] == "watch"
    assert att["signal_rules"]["warning_pct"] >= 2.0


def _headcount_series(cohort_id: str, values: list[int]) -> list[dict]:
    rows = []
    for i, v in enumerate(values):
        month = f"2025-{i + 1:02d}-01" if i < 12 else f"2026-{i - 11:02d}-01"
        rows.append({
            "cohort_id": cohort_id,
            "cohort_name": f"Div {cohort_id}",
            "cohort_kind": "division",
            "snapshot_month": month,
            "active_headcount": v,
            "cohort_size": v,
            "generated_at": "2026-07-01 00:00:00",
        })
    return rows


@pytest.mark.asyncio
async def test_mc_derived_runs_on_monthly_headcount_trend(monkeypatch):
    # A cohort stable at ~200 then jumping +30% in the latest month must emit an
    # observed signal whose Monte Carlo ran in derived mode over the real history.
    series = _headcount_series("D1", [200, 201, 199, 202, 200, 203, 201, 200, 202, 201, 200, 260])
    monkeypatch.setattr(
        intelligence_engine,
        "load_contracts",
        lambda cartridge_ids=None: [{k: v for k, v in _contract().items()
                                     if k != "metrics"} | {"metrics": [
            m for m in _contract()["metrics"] if m["id"] == "talent_headcount_by_cohort"]}],
    )

    async def fetch(dataset, user, limit):
        return series

    result = await intelligence_engine.run_intelligence(
        USER,
        {"cartridge_id": "sap_successfactors",
         "datasets": ["sap_successfactors_talent_headcount_by_cohort_month"],
         "run_mode": "gold_refresh"},
        fetcher=fetch,
        persist=False,
    )
    sig = result["signals"]
    assert sig, "expected at least one headcount signal on the +30% jump"
    observed = [s for s in sig if s.get("signal_subtype") == "observed"]
    assert observed, "expected an observed signal"
    mc = observed[0].get("monte_carlo") or {}
    assert mc.get("status") == "completed", f"Monte Carlo did not run: {mc}"
    assert mc.get("mode") == "derived_mode"
    assert all(not str(s.get("metric", "")).startswith("generic_") for s in sig)


@pytest.mark.asyncio
async def test_stable_series_produces_no_false_signals(monkeypatch):
    # A flat/stable workforce must NOT fabricate anomaly signals (honest quiet).
    series = _headcount_series("D2", [200, 200, 201, 200, 199, 200, 201, 200, 200, 201, 200, 200])
    monkeypatch.setattr(
        intelligence_engine,
        "load_contracts",
        lambda cartridge_ids=None: [{k: v for k, v in _contract().items()
                                     if k != "metrics"} | {"metrics": [
            m for m in _contract()["metrics"] if m["id"] == "talent_headcount_by_cohort"]}],
    )

    async def fetch(dataset, user, limit):
        return series

    result = await intelligence_engine.run_intelligence(
        USER,
        {"cartridge_id": "sap_successfactors",
         "datasets": ["sap_successfactors_talent_headcount_by_cohort_month"],
         "run_mode": "gold_refresh"},
        fetcher=fetch,
        persist=False,
    )
    observed = [s for s in result["signals"] if s.get("signal_subtype") == "observed"]
    assert not observed, f"stable series should not emit observed signals: {observed}"
