from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.routers import intelligence as intelligence_router
from app.services.intelligence import calibration_service


TENANT = "11111111-1111-1111-1111-111111111111"
WORKSPACE = "22222222-2222-2222-2222-222222222222"
USER = {
    "id": 7,
    "active_tenant_id": TENANT,
    "active_workspace_id": WORKSPACE,
}


def _payload(outcome_id: str = "41", **overrides: Any) -> dict[str, Any]:
    payload = {
        "source_type": "prediction_outcome",
        "source_id": outcome_id,
        "predicted_metric": "margin",
        "predicted_value": 10,
        "actual_value": 12 if outcome_id == "41" else 14,
        "actual_status": "unknown",
        "horizon_days": 30,
    }
    payload.update(overrides)
    return payload


class _Store:
    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.observations: dict[str, dict[str, Any]] = {}
        self.state: dict[str, Any] | None = None
        self.outcomes = {"41": 12.0, "42": 14.0}
        self.observation_inserts = 0
        self.state_upserts = 0


class _Transaction:
    def __init__(self, store: _Store) -> None:
        self.store = store

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        if self.store.lock.locked():
            self.store.lock.release()


class _Connection:
    def __init__(self, store: _Store) -> None:
        self.store = store

    def transaction(self):
        return _Transaction(self.store)

    async def execute(self, sql: str, *_params):
        if "pg_advisory_xact_lock" in sql:
            await self.store.lock.acquire()

    async def fetchrow(self, sql: str, *params):
        if "FROM prediction_outcomes outcome" in sql:
            workspace, outcome_id, tenant = params
            value = self.store.outcomes.get(outcome_id)
            if workspace != WORKSPACE or tenant != TENANT or value is None:
                return None
            return {
                "outcome_id": outcome_id,
                "outcome_tenant_id": TENANT,
                "signal_id": "signal-a",
                "option_id": None,
                "option_matches": True,
                "action_taken": "review",
                "actual_value": value,
                "outcome_created_at": datetime(
                    2026, 7, 30, int(outcome_id) - 40, tzinfo=timezone.utc
                ),
                "metric": "margin",
                "signal_predicted_value": 10,
                "signal_subtype": "observed",
                "source_system": "replicon",
                "source_dataset": "gold_margin",
                "evidence_pack_id": "evidence-real",
                "prediction_horizon_days": 30,
                "signal_metadata": {"observed": True},
            }
        if "FROM calibration_observations" in sql:
            return self.store.observations.get(params[1])
        if "SELECT *" in sql and "FROM calibration_states" in sql:
            return self.store.state
        if "INSERT INTO calibration_observations" in sql:
            key = params[1]
            if key in self.store.observations:
                return None
            self.store.observation_inserts += 1
            row = {
                "id": self.store.observation_inserts,
                "observation_id": params[0],
                "idempotency_key": key,
                "evidence_digest": params[2],
                "tenant_id": params[3],
                "workspace_id": params[4],
                "source_type": params[5],
                "source_id": params[6],
                "predicted_metric": params[7],
                "predicted_value": params[8],
                "actual_value": params[11],
                "actual_status": params[12],
                "observed_at": params[13],
                "model_version": params[15],
                "calibration_group": params[16],
                "metrics": json.loads(params[19]),
                "reproducibility_hash": params[22],
            }
            self.store.observations[key] = row
            return row
        if "INSERT INTO calibration_states" in sql:
            self.store.state_upserts += 1
            self.store.state = {
                "id": 1,
                "state_id": params[0],
                "tenant_id": params[1],
                "workspace_id": params[2],
                "calibration_group": params[3],
                "model_version": params[4],
                "prior": json.loads(params[5]),
                "posterior": json.loads(params[6]),
                "metrics": json.loads(params[7]),
                "sample_count": params[8],
                "unknown_count": params[12],
                "reproducibility_hash": params[20],
            }
            return self.store.state
        raise AssertionError(sql)


class _Acquire:
    def __init__(self, conn: _Connection) -> None:
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *_args):
        return None


class _Pool:
    def __init__(self, store: _Store) -> None:
        self.store = store

    def acquire(self):
        return _Acquire(_Connection(self.store))


def _patch_pool(monkeypatch, store: _Store) -> None:
    monkeypatch.setattr(
        calibration_service.auth,
        "pool",
        AsyncMock(return_value=_Pool(store)),
    )


@pytest.mark.asyncio
async def test_sequential_and_timeout_retry_return_one_receipt(monkeypatch) -> None:
    store = _Store()
    _patch_pool(monkeypatch, store)
    first = await calibration_service.observe(USER, _payload())
    retry_after_lost_response = await calibration_service.observe(USER, _payload())
    assert retry_after_lost_response == first
    assert (store.observation_inserts, store.state_upserts) == (1, 1)
    assert first["state"]["sample_count"] == 1


@pytest.mark.asyncio
async def test_concurrent_replay_is_serialized_and_calibrates_once(monkeypatch) -> None:
    store = _Store()
    _patch_pool(monkeypatch, store)
    left, right = await asyncio.gather(
        calibration_service.observe(USER, _payload()),
        calibration_service.observe(USER, _payload()),
    )
    assert left == right
    assert (store.observation_inserts, store.state_upserts) == (1, 1)


@pytest.mark.asyncio
async def test_distinct_evidence_creates_distinct_observation(monkeypatch) -> None:
    store = _Store()
    _patch_pool(monkeypatch, store)
    await calibration_service.observe(USER, _payload("41"))
    second = await calibration_service.observe(USER, _payload("42"))
    assert len(store.observations) == 2
    assert second["state"]["sample_count"] == 2


@pytest.mark.asyncio
async def test_changed_evidence_same_identity_conflicts_without_state_change(
    monkeypatch,
) -> None:
    store = _Store()
    _patch_pool(monkeypatch, store)
    await calibration_service.observe(USER, _payload())
    original_state = dict(store.state or {})
    store.outcomes["41"] = 13
    with pytest.raises(HTTPException) as exc:
        await calibration_service.observe(USER, _payload(actual_value=13))
    assert (exc.value.status_code, exc.value.detail) == (
        409,
        "calibration evidence conflict",
    )
    assert store.state == original_state
    assert (store.observation_inserts, store.state_upserts) == (1, 1)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        _payload(actual_status="hit", actual_value=-999999),
        _payload(source_type="decision_option", source_id="option-a"),
    ],
)
async def test_rejected_claims_never_modify_observation_or_state(
    monkeypatch,
    payload: dict[str, Any],
) -> None:
    store = _Store()
    _patch_pool(monkeypatch, store)
    with pytest.raises(HTTPException):
        await calibration_service.observe(USER, payload)
    assert store.observations == {}
    assert store.state is None
    assert (store.observation_inserts, store.state_upserts) == (0, 0)


def test_two_identical_http_posts_return_same_observation(monkeypatch) -> None:
    store = _Store()
    _patch_pool(monkeypatch, store)
    app = FastAPI()

    @app.post("/calibration/observe")
    async def observe(body: intelligence_router.CalibrationObservationRequest):
        return await intelligence_router.intelligence_calibration_observe(body, USER)

    client = TestClient(app)
    first = client.post("/calibration/observe", json=_payload())
    second = client.post("/calibration/observe", json=_payload())
    assert (first.status_code, second.status_code) == (200, 200)
    assert first.json() == second.json()
    assert (store.observation_inserts, store.state_upserts) == (1, 1)
