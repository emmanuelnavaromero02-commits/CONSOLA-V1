from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from app.services.intelligence import backtesting


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
    def __init__(self, snapshot_rows: list[dict] | None = None):
        self.snapshot_rows = snapshot_rows or []
        self.executed: list[tuple[str, tuple]] = []
        self.fetches: list[tuple[str, tuple]] = []
        self.inserted_results: list[dict] = []
        self.finished_summary: dict | None = None

    def transaction(self):
        return _FakeTransaction()

    async def execute(self, query: str, *args):
        self.executed.append((query, args))
        if "INSERT INTO backtest_results" in query:
            self.inserted_results.append(
                {
                    "snapshot_id": args[3],
                    "signal_id": args[4],
                    "source_system": args[6],
                    "source_dataset": args[7],
                    "gold_table": args[8],
                    "metric": args[9],
                    "entity_key": args[10],
                    "period_key": args[11],
                    "as_of_period": args[12],
                    "method": args[13],
                    "anomaly_probability": args[14],
                    "predicted_label": args[15],
                    "actual_label": args[16],
                    "label_source": args[17],
                    "recommended_decision": args[18],
                    "uncertainty_level": args[19],
                    "data_quality_status": args[20],
                    "expected_impact_value": args[21],
                    "measured_impact": args[22],
                    "is_true_positive": args[24],
                    "is_false_positive": args[25],
                    "is_true_negative": args[26],
                    "is_false_negative": args[27],
                    "result": json.loads(args[28]),
                }
            )

    async def fetchrow(self, query: str, *args):
        self.fetches.append((query, args))
        if "INSERT INTO backtest_runs" in query:
            return {
                "id": 101,
                "tenant_id": args[0],
                "workspace_id": args[1],
                "run_ref": args[2],
                "run_mode": args[3],
                "source_system": args[4],
                "source_dataset": args[5],
                "metric": args[6],
                "status": "running",
                "labels_required": args[8],
                "summary": {},
                "config": json.loads(args[9]),
            }
        if "UPDATE backtest_runs" in query and "RETURNING" in query:
            self.finished_summary = json.loads(args[7])
            return {
                "id": args[1],
                "tenant_id": USER["active_tenant_id"],
                "workspace_id": args[0],
                "run_ref": "backtest-test",
                "run_mode": "fixture_validation",
                "source_system": "replicon",
                "source_dataset": "consultor_mensual",
                "metric": "billable_hours",
                "status": args[2],
                "periods_evaluated": args[3],
                "labels_available": args[4],
                "labels_required": args[5],
                "insufficient_labeled_data": args[6],
                "summary": self.finished_summary,
                "config": {},
            }
        return None

    async def fetch(self, query: str, *args):
        self.fetches.append((query, args))
        if "FROM decision_intelligence_snapshots" in query:
            return self.snapshot_rows
        return []

    async def fetchval(self, query: str, *args):
        self.fetches.append((query, args))
        if "to_regclass" in query:
            return "backtest_runs"
        return None


class _FakeAcquire:
    def __init__(self, conn: _FakeConnection):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        return None


class _FakePool:
    def __init__(self, conn: _FakeConnection):
        self.conn = conn

    def acquire(self):
        return _FakeAcquire(self.conn)


def _rows_with_fixture_labels() -> list[dict]:
    return [
        {"mes": "2026-01-01", "consultor": "Ana", "horas_facturables": 100},
        {"mes": "2026-02-01", "consultor": "Ana", "horas_facturables": 100},
        {"mes": "2026-03-01", "consultor": "Ana", "horas_facturables": 100},
        {
            "mes": "2026-04-01",
            "consultor": "Ana",
            "horas_facturables": 100,
            "actual_label": False,
        },
        {
            "mes": "2026-05-01",
            "consultor": "Ana",
            "horas_facturables": 220,
            "actual_label": True,
        },
    ]


@pytest.mark.asyncio
async def test_fixture_backtest_persists_run_results_metrics_and_no_lookahead(
    monkeypatch,
):
    conn = _FakeConnection()

    async def fake_pool():
        return _FakePool(conn)

    async def fake_fetcher(dataset: str, user: dict | None, limit: int):
        assert dataset == "consultor_mensual"
        assert user == USER
        assert limit == 5000
        return _rows_with_fixture_labels()

    monkeypatch.setattr(backtesting.auth, "pool", fake_pool)
    monkeypatch.setattr(backtesting, "new_backtest_ref", lambda: "backtest-test")

    result = await backtesting.run_backtest(
        USER,
        {
            "source_system": "replicon",
            "metric": "billable_hours",
            "mode": "fixture_validation",
            "labels_required": 2,
        },
        fetcher=fake_fetcher,
    )

    assert result["summary"]["status"] == "ok"
    assert result["summary"]["precision"] == 1.0
    assert result["summary"]["recall"] == 1.0
    assert result["summary"]["brier_score"] is not None
    assert result["summary"]["by_method"] == {"robust_residual_v0": 2}
    assert len(conn.inserted_results) == 2
    first, second = conn.inserted_results
    assert first["label_source"] == "fixture"
    assert first["actual_label"] is False
    assert first["predicted_label"] is False
    assert first["is_true_negative"] is True
    assert second["actual_label"] is True
    assert second["predicted_label"] is True
    assert second["is_true_positive"] is True
    for item in conn.inserted_results:
        proof = item["result"]
        assert proof["no_lookahead"] is True
        assert proof["history_max_period"] < proof["as_of_period"]


@pytest.mark.asyncio
async def test_missing_gold_dataset_persists_dataset_unavailable(monkeypatch):
    conn = _FakeConnection()

    async def fake_pool():
        return _FakePool(conn)

    async def missing_fetcher(dataset: str, user: dict | None, limit: int):
        raise HTTPException(404, f"dataset unavailable: {dataset}")

    monkeypatch.setattr(backtesting.auth, "pool", fake_pool)
    result = await backtesting.run_backtest(
        USER,
        {"source_system": "replicon", "metric": "billable_hours", "labels_required": 2},
        fetcher=missing_fetcher,
    )

    assert result["summary"]["status"] == "dataset_unavailable"
    assert result["run"]["status"] == "dataset_unavailable"
    assert result["summary"]["total_results"] == 0
    assert conn.inserted_results == []


@pytest.mark.asyncio
async def test_outcome_linked_uses_real_outcome_without_rewriting_snapshot(monkeypatch):
    conn = _FakeConnection(
        snapshot_rows=[
            {
                "snapshot_id": 55,
                "signal_id": "intel:abc",
                "control_room_item_id": "intel:abc",
                "source_system": "replicon",
                "source_dataset": "consultor_mensual",
                "gold_table": "gold_consultor_mensual",
                "metric": "billable_hours",
                "entity_key": "Ana",
                "period_key": "2026-05-01",
                "method": "robust_residual_v0",
                "anomaly_probability": 0.95,
                "recommended_decision": "investigate",
                "uncertainty_level": "medium",
                "expected_impact_value": 14400,
                "data_quality_status": "sufficient",
                "decision_intelligence": {
                    "method": "robust_residual_v0",
                    "anomaly_probability": 0.95,
                    "recommended_decision": "investigate",
                    "uncertainty_level": "medium",
                    "expected_impact": {"value": 14400},
                    "data_quality": {"status": "sufficient"},
                },
                "outcome_id": 77,
                "outcome_status": "success",
                "measured_impact": 15000,
                "outcome_observed_at": "2026-06-01T00:00:00+00:00",
            }
        ]
    )

    async def fake_pool():
        return _FakePool(conn)

    monkeypatch.setattr(backtesting.auth, "pool", fake_pool)
    result = await backtesting.run_backtest(
        USER,
        {
            "source_system": "replicon",
            "metric": "billable_hours",
            "mode": "outcome_linked",
            "labels_required": 1,
        },
    )

    assert result["summary"]["status"] == "ok"
    assert conn.inserted_results[0]["snapshot_id"] == 55
    assert conn.inserted_results[0]["label_source"] == "outcome"
    assert conn.inserted_results[0]["actual_label"] is True
    assert conn.inserted_results[0]["measured_impact"] == 15000
    assert not any(
        "UPDATE decision_intelligence_snapshots" in query for query, _ in conn.executed
    )


def test_summary_is_honest_when_labels_are_insufficient_and_skips_null_brier():
    summary = backtesting.summarize_results(
        [
            {
                "actual_label": True,
                "predicted_label": True,
                "label_source": "fixture",
                "anomaly_probability": None,
                "is_true_positive": True,
                "method": "robust_residual_v0",
                "recommended_decision": "investigate",
                "uncertainty_level": "medium",
                "data_quality_status": "sufficient",
            }
        ],
        labels_required=3,
    )

    assert summary["status"] == "insufficient_labeled_data"
    assert summary["precision"] is None
    assert summary["recall"] is None
    assert summary["brier_score"] is None
    assert "Only 1 labeled" in summary["rationale"]


def test_api_and_makefile_expose_backtest_contracts():
    from pathlib import Path

    repo = Path(__file__).resolve().parents[2]
    router = (repo / "console/app/routers/intelligence.py").read_text(encoding="utf-8")
    makefile = (repo / "Makefile").read_text(encoding="utf-8")
    script = (repo / "scripts/run_decision_backtest.py").read_text(encoding="utf-8")

    assert '"/backtests/run"' in router
    assert '"/backtests/{backtest_id}/results"' in router
    assert "decision-backtest-local:" in makefile
    assert "decision-backtest-aws:" in makefile
    assert "CONTROL_ROOM_ENABLE_EXTERNAL_WRITEBACK" in script
    assert "REFUSE: external write-back enabled" in script
    assert "stdout_redacted.txt" in script
    assert "printenv" not in script
    assert "source .env" not in script
