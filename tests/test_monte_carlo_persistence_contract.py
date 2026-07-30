from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.services.intelligence import monte_carlo_service
from app.services.intelligence import monte_carlo


REPO = Path(__file__).resolve().parents[1]


class _FakeConnection:
    def __init__(self):
        self.calls: list[tuple[str, str, tuple]] = []

    def transaction(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, sql: str, *params):
        self.calls.append(("execute", sql, params))

    async def fetchval(self, sql: str, *params):
        self.calls.append(("fetchval", sql, params))
        if "to_regclass" in sql:
            return "backtest_runs"
        return None

    async def fetchrow(self, sql: str, *params):
        self.calls.append(("fetchrow", sql, params))
        if "FROM intelligence_signals" in sql:
            return {
                "signal_subtype": "observed",
                "source_system": "control_room",
                "source_dataset": "metric_observations",
                "evidence_pack_id": "ep-a",
                "metadata": {"observed": True},
            }
        return {
            "id": 1,
            "simulation_id": params[0],
            "tenant_id": params[1],
            "workspace_id": params[2],
            "source_type": params[3],
            "source_id": params[4],
            "horizon_days": params[5],
            "iterations": params[6],
            "seed": params[7],
            "model_version": params[8],
            "input_variables": json.loads(params[9]),
            "assumptions": json.loads(params[10]),
            "output_metric": params[11],
            "distribution_summary": json.loads(params[14]),
            "sensitivity": json.loads(params[15]),
            "option_comparison": json.loads(params[16]),
            "reproducibility_hash": params[18],
        }

    async def fetch(self, sql: str, *params):
        self.calls.append(("fetch", sql, params))
        return []


class _Acquire:
    def __init__(self, conn: _FakeConnection):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakePool:
    def __init__(self):
        self.conn = _FakeConnection()

    def acquire(self):
        return _Acquire(self.conn)


def _payload() -> dict:
    return {
        "source_type": "signal",
        "source_id": "signal-a",
        "iterations": 20,
        "seed": 7,
        "input_variables": {
            "baseline_value": {"type": "fixed", "value": 100},
            "expected_delta": {"type": "fixed", "value": 10},
        },
        "output_metric": "net_value",
    }


@pytest.mark.asyncio
async def test_run_simulation_sets_db_scope_validates_source_and_persists(monkeypatch):
    fake = _FakePool()
    monkeypatch.setattr(monte_carlo_service.auth, "pool", AsyncMock(return_value=fake))

    result = await monte_carlo_service.run_simulation(
        {"id": 42, "tenant_id": "tenant-a", "active_workspace_id": "ws-a"},
        _payload(),
    )

    assert result["simulation"]["simulation_id"].startswith("mc-")
    assert fake.conn.calls[0][0] == "execute"
    assert "set_config('app.tenant_id'" in fake.conn.calls[0][1]
    assert any(
        call[0] == "fetchrow" and "FROM intelligence_signals" in call[1]
        for call in fake.conn.calls
    )
    assert any(
        call[0] == "fetchrow" and "INSERT INTO monte_carlo_simulations" in call[1]
        for call in fake.conn.calls
    )


@pytest.mark.asyncio
async def test_selected_option_persists_its_exact_result_and_assumptions(monkeypatch):
    fake = _FakePool()
    monkeypatch.setattr(monte_carlo_service.auth, "pool", AsyncMock(return_value=fake))
    payload = {
        **_payload(),
        "input_variables": {"baseline_value": {"type": "fixed", "value": 999}},
        "options": [
            {
                "option_id": "small",
                "input_variables": {"baseline_value": {"type": "fixed", "value": 10}},
                "assumptions": {"scenario": "small"},
            },
            {
                "option_id": "large",
                "input_variables": {"baseline_value": {"type": "fixed", "value": 200}},
                "assumptions": {"scenario": "large"},
            },
        ],
    }

    user = {"id": 42, "active_workspace_id": "ws-a"}
    first = await monte_carlo_service.run_simulation(user, payload)
    second = await monte_carlo_service.run_simulation(user, payload)
    simulation = first["simulation"]

    assert simulation["option_comparison"]["ranking"][0]["option_id"] == "large"
    assert simulation["option_comparison"]["selected_option_id"] == "large"
    assert simulation["input_variables"] == {
        "baseline_value": {"type": "fixed", "value": 200.0}
    }
    assert simulation["assumptions"]["scenario"] == "large"
    assert simulation["assumptions"]["input_classification"] == "scenario_assumption"
    assert simulation["assumptions"]["calibration_status"] == "not_calibrated"
    assert simulation["distribution_summary"]["expected_value"] == 200.0
    selected = simulation["option_comparison"]["options"][0]
    assert simulation["seed"] == selected["seed"]
    assert selected["normalized_input_variables"] == simulation["input_variables"]
    assert selected["assumptions"] == simulation["assumptions"]
    assert selected["distribution_summary"] == simulation["distribution_summary"]
    assert selected["sensitivity"] == simulation["sensitivity"]
    assert (
        simulation["reproducibility_hash"]
        == second["simulation"]["reproducibility_hash"]
    )
    reconstructed = monte_carlo.run_single_simulation(
        {
            **payload,
            "seed": simulation["seed"],
            "input_variables": simulation["input_variables"],
            "assumptions": simulation["assumptions"],
            "options": None,
        }
    )
    assert reconstructed["distribution_summary"] == simulation["distribution_summary"]
    assert reconstructed["sensitivity"] == simulation["sensitivity"]


@pytest.mark.asyncio
async def test_run_simulation_accepts_wisdom_bit_without_signal_lookup(monkeypatch):
    fake = _FakePool()
    monkeypatch.setattr(monte_carlo_service.auth, "pool", AsyncMock(return_value=fake))

    result = await monte_carlo_service.run_simulation(
        {"id": 42, "active_workspace_id": "ws-a"},
        {**_payload(), "source_type": "wisdom_bit", "source_id": "WB-TALENTO"},
    )

    assert result["simulation"]["source_type"] == "wisdom_bit"
    assert not any("FROM intelligence_signals" in call[1] for call in fake.conn.calls)


def test_monte_carlo_migration_is_scoped_and_does_not_relax_rls():
    sql = (REPO / "infra/init/99q_monte_carlo_simulations.sql").read_text(
        encoding="utf-8"
    )

    assert "CREATE TABLE IF NOT EXISTS monte_carlo_simulations" in sql
    for column in (
        "tenant_id",
        "workspace_id",
        "simulation_id",
        "source_type",
        "input_variables",
        "distribution_summary",
        "sensitivity",
        "option_comparison",
        "reproducibility_hash",
    ):
        assert column in sql
    assert "ENABLE ROW LEVEL SECURITY" in sql
    assert "FORCE ROW LEVEL SECURITY" in sql
    assert "ALTER ROLE omega_console NOBYPASSRLS" in sql
    assert "current_setting('app.workspace_id', true)" in sql
    assert "'wisdom_bit'" in sql
    assert "USING (true)" not in sql
    assert "WITH CHECK (true)" not in sql


def test_monte_carlo_wisdom_bit_source_migration_preserves_rls():
    sql = (REPO / "infra/init/99z_monte_carlo_wisdom_bit_source.sql").read_text(
        encoding="utf-8"
    )

    assert "monte_carlo_simulations_source_type_check" in sql
    assert "'wisdom_bit'" in sql
    assert "ALTER TABLE monte_carlo_simulations" in sql
    assert "ENABLE ROW LEVEL SECURITY" not in sql
    assert "DISABLE ROW LEVEL SECURITY" not in sql
