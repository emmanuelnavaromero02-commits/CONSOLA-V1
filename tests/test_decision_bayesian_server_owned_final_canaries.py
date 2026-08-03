from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.services.intelligence import calibration, calibration_service
from app.services.intelligence.calibration_recompute_batch import load_complete_batch
from app.services.intelligence import orchestrator_execution
from app.routers import intelligence as intelligence_router


FORGED_VERSION = "client.forged.v999"


def _observation_payload() -> dict:
    return {
        "source_type": "prediction_outcome",
        "source_id": "outcome-a",
        "actual_status": "hit",
        "predicted_metric": "net_value",
        "predicted_probability": 0.8,
        "model_version": FORGED_VERSION,
    }


def test_observe_rejects_client_model_version() -> None:
    with pytest.raises(HTTPException, match="server-owned") as exc:
        calibration_service._validate_payload(_observation_payload())
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_recompute_rejects_client_model_version_before_database(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        calibration_service.auth,
        "pool",
        AsyncMock(side_effect=AssertionError("database must not be queried")),
    )
    with pytest.raises(HTTPException, match="server-owned") as exc:
        await calibration_service.recompute(
            {"id": 7, "active_workspace_id": "ws-a"},
            {"calibration_group": "global", "model_version": FORGED_VERSION},
        )
    assert exc.value.status_code == 422


class EmptyBatchConnection:
    async def fetchval(self, _sql: str, *_params):
        return 0


@pytest.mark.asyncio
async def test_zero_trusted_batch_is_incomplete_with_explicit_reason() -> None:
    rows, metrics = await load_complete_batch(
        EmptyBatchConnection(),
        workspace_id="ws-a",
        group="global",
        model_version=calibration.MODEL_VERSION,
        source_type=None,
        source_id=None,
        operational_limit=5000,
        allow_manual=False,
    )
    assert rows == []
    assert metrics["processed_total"] == 0
    assert metrics["complete"] is False
    assert metrics["reason"] == "no_trusted_observations"


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [0, 3])
async def test_zero_trusted_recompute_never_upserts_state(monkeypatch, count) -> None:
    from tests.test_calibration_recompute_complete_batch import (
        _Pool,
        _ScopedConnection,
        _row,
    )

    monkeypatch.setenv("APP_ENV", "production")
    conn = _ScopedConnection([_row(index) for index in range(1, count + 1)])
    monkeypatch.setattr(
        calibration_service.auth, "pool", AsyncMock(return_value=_Pool(conn))
    )
    with pytest.raises(HTTPException) as exc:
        await calibration_service.recompute(
            {"id": 7, "active_workspace_id": "ws-a"},
            {"calibration_group": "global"},
        )
    assert exc.value.status_code == 409
    assert exc.value.detail["reason"] == "no_trusted_observations"
    assert exc.value.detail["processed_total"] == 0
    assert exc.value.detail["skipped_total"] == count
    assert not any("INSERT INTO calibration_states" in sql for sql in conn.calls)


def test_http_observe_then_execute_rejects_forged_version_without_db(
    monkeypatch,
) -> None:
    unavailable = AsyncMock(side_effect=AssertionError("database must not be queried"))
    monkeypatch.setattr(calibration_service.auth, "pool", unavailable)
    monkeypatch.setattr(orchestrator_execution.auth, "pool", unavailable)
    user = {"id": 7, "active_workspace_id": "ws-a"}
    app = FastAPI()

    @app.post("/calibration/observe")
    async def observe(body: intelligence_router.CalibrationObservationRequest):
        return await intelligence_router.intelligence_calibration_observe(body, user)

    @app.post("/orchestrate/{orchestration_id}/execute-engines")
    async def execute(
        orchestration_id: str,
        body: intelligence_router.OrchestrationExecuteEnginesRequest,
    ):
        return await intelligence_router.intelligence_orchestration_execute_engines(
            orchestration_id, body, user
        )

    client = TestClient(app, raise_server_exceptions=False)
    observation = client.post("/calibration/observe", json=_observation_payload())
    execution = client.post(
        "/orchestrate/orch-a/execute-engines",
        json={
            "engine_inputs": {
                "bayesian_calibration": {
                    "calibration_group": "global",
                    "model_version": FORGED_VERSION,
                }
            }
        },
    )
    assert (observation.status_code, execution.status_code) == (422, 422)
    assert observation.json()["detail"] == "model_version is server-owned"
    assert execution.json()["detail"] == "model_version is server-owned"
    unavailable.assert_not_awaited()


class BayesianLookupConnection:
    def __init__(
        self, *, sample_count: int = 24, source_version: str | None = None
    ) -> None:
        self.sample_count = sample_count
        self.source_version = source_version
        self.params: list[tuple] = []

    async def fetchrow(self, sql: str, *params):
        self.params.append(params)
        if "FROM calibration_observations" in sql:
            return {
                "calibration_group": "risk:late",
                "model_version": self.source_version,
            }
        if "FROM calibration_states" in sql:
            return {
                "calibration_group": "risk:late",
                "model_version": calibration.MODEL_VERSION,
                "posterior": {"mean": 0.7},
                "metrics": {
                    "sample_count": self.sample_count,
                    "complete": True,
                    "provenance_complete": True,
                },
                "sample_count": self.sample_count,
            }
        raise AssertionError(sql)


@pytest.mark.asyncio
async def test_execute_engines_rejects_forged_bayesian_version() -> None:
    conn = BayesianLookupConnection()
    with pytest.raises(
        orchestrator_execution.OrchestratorExecutionError, match="server-owned"
    ) as exc:
        await orchestrator_execution._run_bayesian_lookup(
            conn,
            workspace_id="ws-a",
            run={"source_type": "signal", "source_id": "signal-a"},
            engine_inputs={
                "bayesian_calibration": {
                    "calibration_group": "risk:late",
                    "model_version": FORGED_VERSION,
                }
            },
        )
    assert exc.value.status_code == 422
    assert conn.params == []


@pytest.mark.asyncio
async def test_historical_observation_cannot_select_noncanonical_state() -> None:
    conn = BayesianLookupConnection(source_version=FORGED_VERSION)
    await orchestrator_execution._run_bayesian_lookup(
        conn,
        workspace_id="ws-a",
        run={"source_type": "calibration_observation", "source_id": "obs-a"},
        engine_inputs={"bayesian_calibration": {}},
    )
    state_query_params = conn.params[-1]
    assert state_query_params[2] == calibration.MODEL_VERSION


@pytest.mark.asyncio
async def test_orchestrator_never_succeeds_with_zero_sample_state() -> None:
    result = await orchestrator_execution._run_bayesian_lookup(
        BayesianLookupConnection(sample_count=0),
        workspace_id="ws-a",
        run={"source_type": "signal", "source_id": "signal-a"},
        engine_inputs={"bayesian_calibration": {"calibration_group": "risk:late"}},
    )
    assert result[0] == "skipped"
    assert result[1] == {"status": "skipped", "reason": "no_trusted_observations"}
