from __future__ import annotations

import math
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.routers import intelligence as intelligence_router
from app.services.intelligence import monte_carlo, monte_carlo_service


def _payload(value: float, *, options: list[dict] | None = None) -> dict:
    payload = {
        "source_type": "signal",
        "source_id": "signal-observed",
        "iterations": 2,
        "seed": 7,
        "output_metric": "net_value",
        "input_variables": {
            "baseline_value": {"type": "fixed", "value": value},
            "revenue_growth": {"type": "fixed", "value": 10},
        },
    }
    if options is not None:
        payload["options"] = options
    return payload


@pytest.mark.parametrize("value", [1e308, -1e308])
def test_material_overflow_fails_typed_and_sanitized(value: float) -> None:
    with pytest.raises(
        monte_carlo.MonteCarloValidationError,
        match=r"^monte carlo produced a non-finite result$",
    ) as exc:
        monte_carlo.run_monte_carlo(_payload(value))
    message = str(exc.value)
    assert all(token not in message for token in ("Infinity", "NaN", "1e+308"))


def test_valid_extreme_control_remains_finite() -> None:
    result = monte_carlo.run_monte_carlo(
        _payload(1e100)
    )
    summary = result["distribution_summary"]
    assert all(
        math.isfinite(float(summary[key]))
        for key in ("mean", "median", "p10", "p50", "p90", "min", "max")
    )


def test_later_option_overflow_aborts_whole_comparison() -> None:
    options = [
        {
            "option_id": "valid",
            "input_variables": {
                "baseline_value": {"type": "fixed", "value": 100},
            },
        },
        {
            "option_id": "overflow",
            "input_variables": {
                "baseline_value": {"type": "fixed", "value": 1e308},
                "revenue_growth": {"type": "fixed", "value": 10},
            },
        },
    ]
    with pytest.raises(monte_carlo.MonteCarloValidationError):
        monte_carlo.run_monte_carlo(_payload(100, options=options))


class _Connection:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def execute(self, sql: str, *_params) -> None:
        self.calls.append(sql)

    async def fetchrow(self, sql: str, *_params):
        self.calls.append(sql)
        if "FROM intelligence_signals" in sql:
            return {
                "signal_subtype": "observed",
                "source_system": "replicon",
                "source_dataset": "gold_observations",
                "evidence_pack_id": "evidence-real",
                "metadata": {"observed": True},
            }
        raise AssertionError("overflow must fail before persistence")


@pytest.mark.asyncio
async def test_http_service_overflow_is_422_with_zero_persistence(monkeypatch) -> None:
    conn = _Connection()
    monkeypatch.setattr(
        monte_carlo_service.auth,
        "pool",
        AsyncMock(return_value=conn),
    )
    with pytest.raises(HTTPException) as exc:
        await monte_carlo_service.run_simulation(
            {
                "id": 7,
                "active_tenant_id": "tenant-a",
                "active_workspace_id": "workspace-a",
            },
            _payload(1e308),
        )
    assert exc.value.status_code == 422
    assert exc.value.detail == "monte carlo produced a non-finite result"
    assert not any(
        token in sql
        for sql in conn.calls
        for token in (
            "INSERT INTO monte_carlo_simulations",
            "INSERT INTO calibration_observations",
            "INSERT INTO evidence",
        )
    )


def test_real_http_boundary_returns_sanitized_422(monkeypatch) -> None:
    conn = _Connection()
    monkeypatch.setattr(
        monte_carlo_service.auth,
        "pool",
        AsyncMock(return_value=conn),
    )
    user = {
        "id": 7,
        "active_tenant_id": "tenant-a",
        "active_workspace_id": "workspace-a",
    }
    app = FastAPI()

    @app.post("/monte-carlo/run")
    async def run(body: intelligence_router.MonteCarloRunRequest):
        return await intelligence_router.intelligence_monte_carlo_run(body, user)

    response = TestClient(app).post("/monte-carlo/run", json=_payload(1e308))
    assert response.status_code == 422
    assert response.json() == {
        "detail": "monte carlo produced a non-finite result"
    }
    assert all(token not in response.text for token in ("Infinity", "NaN", "1e+308"))
