from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.services.intelligence import calibration_service


NONLOCAL_ENVS = [None, "", "production", "unknown"]


def _set_app_env(monkeypatch, value: str | None) -> None:
    if value is None:
        monkeypatch.delenv("APP_ENV", raising=False)
    else:
        monkeypatch.setenv("APP_ENV", value)


def _row(*, row_id: int, source_type: str, source_id: str) -> dict:
    return {
        "id": row_id,
        "observed_at": f"2026-01-{row_id:02d}T00:00:00Z",
        "source_type": source_type,
        "source_id": source_id,
        "actual_status": "hit",
        "predicted_metric": "net_value",
        "predicted_probability": 0.8,
        "predicted_value": 100.0,
        "predicted_interval": {"low": 80.0, "high": 120.0},
        "actual_value": 110.0,
        "calibration_group": "monte_carlo",
        "model_version": "cal.test.v1",
        "evidence_refs": [],
    }


class _Connection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, tuple]] = []
        self.observation_rows = [
            _row(row_id=1, source_type="monte_carlo_simulation", source_id="mc-real"),
            _row(row_id=2, source_type="manual_fixture", source_id="fixture-history"),
            _row(
                row_id=3,
                source_type="monte_carlo_simulation",
                source_id="mc-manual",
            ),
        ]

    def transaction(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def execute(self, sql: str, *params):
        self.calls.append(("execute", sql, params))

    async def fetch(self, sql: str, *params):
        self.calls.append(("fetch", sql, params))
        if "FROM calibration_observations" not in sql:
            return []
        rows = list(self.observation_rows)
        return rows

    async def fetchval(self, sql: str, *params):
        self.calls.append(("fetchval", sql, params))
        if "COUNT(*) FROM calibration_observations" in sql:
            return len(self.observation_rows)
        raise AssertionError(f"unexpected fetchval: {sql[:80]}")

    async def fetchrow(self, sql: str, *params):
        self.calls.append(("fetchrow", sql, params))
        if "SELECT 1 AS trusted FROM control_room_items" in sql:
            return {"trusted": 1}
        if "FROM monte_carlo_simulations" in sql:
            return {
                "source_type": (
                    "manual_fixture" if params[1] == "mc-manual" else "wisdom_bit"
                ),
                "source_id": (
                    "fixture-history" if params[1] == "mc-manual" else "WB-TALENTO"
                ),
            }
        if "SELECT *" in sql and "FROM calibration_states" in sql:
            return None
        if "INSERT INTO calibration_states" in sql:
            return {
                "state_id": params[0],
                "tenant_id": params[1],
                "workspace_id": params[2],
                "calibration_group": params[3],
                "model_version": params[4],
                "sample_count": params[8],
                "confidence_score": params[18],
            }
        raise AssertionError(f"unexpected fetchrow: {sql[:80]}")


class _Pool:
    def __init__(self) -> None:
        self.conn = _Connection()

    def acquire(self):
        return self.conn


def _payload(**extra) -> dict:
    return {
        "calibration_group": "monte_carlo",
        "model_version": "cal.test.v1",
        **extra,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("app_env", NONLOCAL_ENVS)
async def test_recompute_excludes_historical_manual_fixtures_by_default(
    monkeypatch, app_env
):
    _set_app_env(monkeypatch, app_env)
    pool = _Pool()
    monkeypatch.setattr(calibration_service.auth, "pool", AsyncMock(return_value=pool))

    result = await calibration_service.recompute(
        {"id": 42, "tenant_id": "tenant-a", "active_workspace_id": "ws-a"},
        _payload(),
    )

    selection = next(
        call
        for call in pool.conn.calls
        if "FROM calibration_observations" in call[1]
        and "ORDER BY observed_at ASC" in call[1]
    )
    assert "ORDER BY observed_at ASC, id ASC" in selection[1]
    assert result["observations_recomputed"] == 1
    assert len(pool.conn.observation_rows) == 3
    assert not any(
        "DELETE FROM calibration_observations" in call[1]
        or "UPDATE calibration_observations" in call[1]
        for call in pool.conn.calls
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("app_env", NONLOCAL_ENVS)
async def test_recompute_rejects_explicit_manual_fixture_filter(monkeypatch, app_env):
    _set_app_env(monkeypatch, app_env)
    monkeypatch.setattr(
        calibration_service.auth,
        "pool",
        AsyncMock(side_effect=AssertionError("database must not be queried")),
    )

    with pytest.raises(HTTPException) as exc:
        await calibration_service.recompute(
            {"id": 42, "active_workspace_id": "ws-a"},
            _payload(source_type="manual_fixture"),
        )

    assert exc.value.status_code == 403


class _HistoricalSimulationSource:
    def __init__(self, source_type: str, source_id: str = "fixture-history") -> None:
        self.source_type = source_type
        self.source_id = source_id

    async def fetchrow(self, sql: str, *params):
        if "FROM monte_carlo_simulations" in sql:
            return {"source_type": self.source_type, "source_id": self.source_id}
        if "FROM intelligence_signals" in sql:
            return {
                "signal_subtype": "observed",
                "source_system": "control_room",
                "source_dataset": "metric_observations",
                "evidence_pack_id": "ep-real",
                "metadata": {"observed": True},
            }
        raise AssertionError(f"unexpected fetchrow: {sql[:80]}")

    async def fetchval(self, sql: str, *params):
        raise AssertionError(f"unexpected fetchval: {sql[:80]}")


@pytest.mark.asyncio
async def test_historical_manual_simulation_cannot_recalibrate_bayesian():
    exists = await calibration_service._source_exists(
        _HistoricalSimulationSource("manual_fixture"),
        workspace_id="ws-a",
        source_type="monte_carlo_simulation",
        source_id="mc-manual",
    )

    assert exists is False


@pytest.mark.asyncio
async def test_historical_simulation_requires_observed_nested_provenance():
    exists = await calibration_service._source_exists(
        _HistoricalSimulationSource("signal", "signal-real"),
        workspace_id="ws-a",
        source_type="monte_carlo_simulation",
        source_id="mc-real",
    )

    assert exists is True
