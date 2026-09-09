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
from app.services.control_room.business_runtime_evidence import (
    runtime_row_evidence_fields,
)
from app.services.intelligence import calibration_service


TENANT = "11111111-1111-1111-1111-111111111111"
WORKSPACE = "22222222-2222-2222-2222-222222222222"
USER = {
    "id": 7,
    "active_tenant_id": TENANT,
    "active_workspace_id": WORKSPACE,
}


@pytest.fixture(autouse=True)
def _enable_engine_under_test(monkeypatch):
    monkeypatch.setenv("INTELLIGENCE_MATH_ENGINES_ENABLED", "true")


def _outcome_metadata(outcome_id: str, value: float) -> dict[str, Any]:
    observed_at = datetime(2026, 7, 31, int(outcome_id) - 40, tzinfo=timezone.utc)
    observed = {
        "signal_id": f"signal-observed-{outcome_id}",
        "metric": "margin",
        "actual_value": value,
    }
    evidence = runtime_row_evidence_fields(
        source_dataset="gold_margin",
        source_system="replicon",
        cartridge="replicon",
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        source_row=observed,
        locator_field="signal_id",
        locator_relation="intelligence_signals",
        observed_at=observed_at.isoformat(),
        business_observation={
            **observed,
            "kind": "signal",
            "metric_type": "scalar",
            "observed_value": value,
            "observed_at": observed_at.isoformat(),
        },
    )
    assert evidence
    return {"observed_signal_id": observed["signal_id"], **evidence}


def _payload(outcome_id: str = "41", **overrides: Any) -> dict[str, Any]:
    payload = {
        "source_type": "prediction_outcome",
        "source_id": outcome_id,
        "predicted_metric": "margin",
        "predicted_value": 10,
        "actual_value": 12 if outcome_id == "41" else 14,
        "actual_status": "hit",
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
        self.evaluated = True
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

    async def fetchval(self, sql: str, *_params):
        if "FROM calibration_group_hierarchy" in sql:
            return None
        raise AssertionError(sql)

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
                "outcome_metadata": _outcome_metadata(outcome_id, value),
                "outcome_created_at": datetime(
                    2026, 7, 30, int(outcome_id) - 40, tzinfo=timezone.utc
                ),
                "evaluation_status": "hit" if self.store.evaluated else None,
                "evaluation_rule_version": (
                    "margin-evaluation.v1" if self.store.evaluated else None
                ),
                "evaluated_at": (
                    datetime(2026, 7, 31, int(outcome_id) - 40, tzinfo=timezone.utc)
                    if self.store.evaluated
                    else None
                ),
                "evaluated_by": (
                    "omega_outcome_evaluator.v1" if self.store.evaluated else None
                ),
                "metric": "margin",
                "signal_predicted_value": 10,
                "signal_subtype": "future_opportunity",
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
        if "record_calibration_observation" in sql:
            payload = json.loads(params[0])
            key = payload["idempotency_key"]
            if key in self.store.observations:
                return None
            self.store.observation_inserts += 1
            row = {
                "id": self.store.observation_inserts,
                **payload,
                "tenant_id": TENANT,
                "workspace_id": WORKSPACE,
                "idempotency_key": key,
            }
            self.store.observations[key] = row
            return row
        if "upsert_calibration_state" in sql:
            payload = json.loads(params[0])
            metrics = payload["metrics"]
            self.store.state_upserts += 1
            self.store.state = {
                "id": 1,
                **payload,
                "tenant_id": TENANT,
                "workspace_id": WORKSPACE,
                "sample_count": int(metrics.get("sample_count") or 0),
                "unknown_count": int(metrics.get("unknown_count") or 0),
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
    assert second["state"]["metrics"]["complete"] is False
    assert second["state"]["metrics"]["provenance_complete"] is False
    assert second["state"]["metrics"]["reason"] == "authoritative_recompute_required"


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
        _payload(actual_status="miss", actual_value=-999999),
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


@pytest.mark.asyncio
async def test_ten_unevaluated_outcomes_never_write_or_publish_state(
    monkeypatch,
) -> None:
    store = _Store()
    store.evaluated = False
    _patch_pool(monkeypatch, store)
    for _ in range(10):
        with pytest.raises(HTTPException) as exc:
            await calibration_service.observe(USER, _payload())
        assert exc.value.detail["reason"] == "authoritative_evaluation_unavailable"
        assert exc.value.detail["calibratable_sample_count"] == 0
    assert store.observations == {}
    assert store.state is None
    assert (store.observation_inserts, store.state_upserts) == (0, 0)
