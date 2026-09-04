from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import intelligence as intelligence_router
from app.services.intelligence import monte_carlo, monte_carlo_service


@pytest.fixture(autouse=True)
def _enable_engine_under_test(monkeypatch):
    monkeypatch.setenv("INTELLIGENCE_MATH_ENGINES_ENABLED", "true")


def _payload(weights: list[float], *, iterations: int = 4000) -> dict:
    return {
        "source_type": "signal",
        "source_id": "signal-observed",
        "iterations": iterations,
        "seed": 17,
        "output_metric": "net_value",
        "input_variables": {
            "baseline_value": {
                "type": "discrete",
                "values": [
                    {"value": 0, "weight": weights[0]},
                    {"value": 1, "weight": weights[1]},
                ],
            }
        },
    }


def test_huge_equal_weights_remain_a_real_fifty_fifty_distribution() -> None:
    result = monte_carlo.run_monte_carlo(_payload([1e308, 1e308]))
    summary = result["distribution_summary"]
    assert 0.45 <= summary["mean"] <= 0.55
    normalized = result["normalized_input_variables"]["baseline_value"]["values"]
    assert normalized == [
        {"value": 0.0, "weight": 0.5},
        {"value": 1.0, "weight": 0.5},
    ]


@pytest.mark.parametrize("weights", ([1e308, 1.0], [1.0, 1e308]))
def test_very_different_finite_weights_remain_valid(weights: list[float]) -> None:
    result = monte_carlo.run_monte_carlo(_payload(weights, iterations=20))
    normalized = result["normalized_input_variables"]["baseline_value"]["values"]
    assert len(normalized) == 2
    assert sum(item["weight"] for item in normalized) == pytest.approx(1.0)


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
        raise AssertionError("invalid weights must fail before persistence")


def test_http_non_normalizable_weight_is_sanitized_and_has_zero_dml(
    monkeypatch,
) -> None:
    conn = _Connection()
    monkeypatch.setattr(
        monte_carlo_service.auth,
        "pool",
        AsyncMock(return_value=conn),
    )
    app = FastAPI()
    user = {
        "id": 7,
        "active_tenant_id": "tenant-a",
        "active_workspace_id": "workspace-a",
    }

    @app.post("/monte-carlo/run")
    async def run(body: intelligence_router.MonteCarloRunRequest):
        return await intelligence_router.intelligence_monte_carlo_run(body, user)

    payload = _payload([1e308, 1e308])
    payload["input_variables"]["baseline_value"]["values"][1]["weight"] = "1e10000"
    response = TestClient(app).post("/monte-carlo/run", json=payload)
    assert response.status_code == 422
    assert response.json() == {"detail": "monte carlo produced a non-finite result"}
    assert not any("INSERT INTO monte_carlo_simulations" in sql for sql in conn.calls)
