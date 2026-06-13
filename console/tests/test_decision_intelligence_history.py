from __future__ import annotations

import pytest

from app.services.intelligence import history


USER = {
    "id": 7,
    "email": "ops@example.com",
    "role": "admin",
    "workspace_role": "workspace_admin",
    "active_tenant_id": "11111111-1111-1111-1111-111111111111",
    "active_workspace_id": "22222222-2222-2222-2222-222222222222",
}


class _FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None


class _FakeConnection:
    def __init__(self, rows):
        self.rows = rows
        self.executed: list[tuple[str, tuple]] = []
        self.fetches: list[tuple[str, tuple]] = []

    def transaction(self):
        return _FakeTransaction()

    async def execute(self, query: str, *args):
        self.executed.append((query, args))

    async def fetch(self, query: str, *args):
        self.fetches.append((query, args))
        return self.rows

    async def fetchrow(self, query: str, *args):
        self.fetches.append((query, args))
        return {"id": 123}


class _FakeAcquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        return None


class _FakePool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return _FakeAcquire(self.conn)


@pytest.mark.asyncio
async def test_calibration_report_is_honest_when_outcomes_are_insufficient(monkeypatch):
    conn = _FakeConnection(
        [
            {
                "id": 1,
                "method": "robust_baseline_v0",
                "recommended_decision": "investigate",
                "data_quality_status": "sufficient",
                "anomaly_probability": 0.7,
                "expected_impact_value": 1200,
                "measured_impact": None,
                "outcome_id": None,
                "outcome_status": None,
                "calibration_status": "pending_outcome",
            }
        ]
    )

    async def fake_pool():
        return _FakePool(conn)

    monkeypatch.setattr(history.auth, "pool", fake_pool)

    report = await history.calibration_report(USER, min_outcomes_required=3)

    assert report["status"] == "insufficient_outcomes"
    assert report["insufficient_outcomes"] is True
    assert report["total_snapshots"] == 1
    assert report["total_with_outcome"] == 0
    assert report["observed_success_rate"] is None
    assert report["calibration_buckets"] == []
    assert report["by_method"] == {"robust_baseline_v0": 1}
    assert "calibration rates" in report["rationale"]
    assert any("app.workspace_id" in query for query, _ in conn.executed)
    assert "workspace_id = $1" in conn.fetches[0][0]
    assert "tenant_id::text" in conn.fetches[0][0]


@pytest.mark.asyncio
async def test_calibration_report_groups_enough_outcomes_and_skips_null_probability(
    monkeypatch,
):
    conn = _FakeConnection(
        [
            {
                "id": 1,
                "method": "robust_baseline_v0",
                "recommended_decision": "act_now",
                "data_quality_status": "sufficient",
                "anomaly_probability": 0.85,
                "expected_impact_value": 100,
                "measured_impact": 120,
                "outcome_id": 11,
                "outcome_status": "success",
                "calibration_status": "outcome_recorded",
            },
            {
                "id": 2,
                "method": "future_reserved_state_space",
                "recommended_decision": "monitor",
                "data_quality_status": "thin",
                "anomaly_probability": None,
                "expected_impact_value": 80,
                "measured_impact": -10,
                "outcome_id": 12,
                "outcome_status": "failure",
                "calibration_status": "outcome_recorded",
            },
        ]
    )

    async def fake_pool():
        return _FakePool(conn)

    monkeypatch.setattr(history.auth, "pool", fake_pool)

    report = await history.calibration_report(USER, min_outcomes_required=2)

    assert report["status"] == "ready"
    assert report["insufficient_outcomes"] is False
    assert report["total_with_outcome"] == 2
    assert report["observed_success_rate"] == 0.5
    assert report["avg_expected_impact"] == 90
    assert report["avg_measured_impact"] == 55
    assert report["by_recommended_decision"] == {"act_now": 1, "monitor": 1}
    assert report["by_data_quality"] == {"sufficient": 1, "thin": 1}
    assert report["calibration_buckets"] == [
        {
            "bucket": "0.8-1.0",
            "sample_count": 1,
            "observed_success_rate": 1.0,
        }
    ]


def test_datasets_from_contracts_is_deduplicated_for_run_history():
    contracts = [
        {
            "cartridge": "replicon",
            "domain": "PSA",
            "metrics": [
                {"id": "billable_hours", "dataset": "consultor_mensual"},
                {"id": "billable_hours", "dataset": "consultor_mensual"},
                {"id": "margin", "dataset": "pnl_mensual"},
            ],
        }
    ]

    assert history.datasets_from_contracts(contracts) == [
        {
            "cartridge": "replicon",
            "domain": "PSA",
            "dataset": "consultor_mensual",
            "metric": "billable_hours",
        },
        {
            "cartridge": "replicon",
            "domain": "PSA",
            "dataset": "pnl_mensual",
            "metric": "margin",
        },
    ]


@pytest.mark.asyncio
async def test_snapshot_persists_original_decision_intelligence_without_outcome():
    conn = _FakeConnection([])
    artifact = {
        "signal": {
            "signal_id": "intel:abc",
            "cartridge_id": "replicon",
            "dataset": "consultor_mensual",
            "source_dataset": "consultor_mensual",
            "gold_table": "gold_consultor_mensual",
            "metric": "billable_hours",
            "entity_kind": "consultant",
            "entity_id": "c1",
            "entity_label": "Consultor Uno",
            "period_key": "2026-06",
            "freshness_at": "2026-06-01",
            "decision_intelligence": {
                "method": "robust_baseline_v0",
                "anomaly_probability": 0.95,
                "uncertainty_level": "medium",
                "recommended_decision": "investigate",
                "expected_impact": {"value": 1500, "currency": "USD"},
                "data_quality": {"status": "sufficient"},
            },
        },
        "evidence_pack": {"id": 55},
    }

    snapshot_id = await history.persist_decision_intelligence_snapshot(
        conn,
        tenant_id=USER["active_tenant_id"],
        workspace_id=USER["active_workspace_id"],
        user=USER,
        artifact=artifact,
        intelligence_run_id=42,
        run_ref="intel-run-test",
    )

    query, args = conn.fetches[0]
    assert snapshot_id == 123
    assert "INSERT INTO decision_intelligence_snapshots" in query
    assert args[2] == "intel:abc"
    assert args[5] == 42
    assert args[6] == "intel-run-test"
    assert args[17] == "investigate"
    assert args[18] == 0.95
    assert args[20] == 1500
    assert args[22] == "sufficient"
    assert args[23] == "robust_baseline_v0"


@pytest.mark.asyncio
async def test_outcome_link_updates_snapshot_linkage_without_rewriting_decision():
    conn = _FakeConnection([])
    outcome_row = {
        "id": 77,
        "metadata": {"source": "control_room", "action_run_id": 12},
        "created_at": "2026-06-13T00:00:00+00:00",
        "actual_value": 250,
    }

    linked = await history.link_outcome_to_snapshot(
        conn,
        tenant_id=USER["active_tenant_id"],
        workspace_id=USER["active_workspace_id"],
        signal_id="intel:abc",
        outcome_row=outcome_row,
        body={
            "measured_impact": 250,
            "outcome_status": "success",
            "evidence": {"observed_at": "2026-06-12T00:00:00+00:00"},
        },
    )

    query, args = conn.fetches[0]
    assert linked is True
    assert "UPDATE decision_intelligence_snapshots" in query
    assert "decision_intelligence =" not in query
    assert args[1] == "intel:abc"
    assert args[2] == 77
    assert args[5] == "success"
    assert args[6] == 250
