from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.services.intelligence import calibration_autopilot as ap

REPO_ROOT = Path(__file__).resolve().parents[1]


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows
        self.queries = []

    async def fetch(self, sql, *args):
        self.queries.append((sql, args))
        return self._rows


class _FakeScope:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn, "tenant-1", "ws-1"

    async def __aexit__(self, *exc):
        return False


def _patch_db(monkeypatch, rows):
    conn = _FakeConn(rows)
    monkeypatch.setattr(ap.auth, "pool", AsyncMock(return_value=object()))
    monkeypatch.setattr(ap, "scoped_db_for_user", lambda pool, user: _FakeScope(conn))
    return conn


def test_observes_each_pending_outcome_with_authoritative_payload(monkeypatch):
    conn = _patch_db(monkeypatch, [{"outcome_id": "o-1"}, {"outcome_id": "o-2"}])
    observe = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(ap.calibration_observation_service, "observe", observe)

    out = asyncio.run(ap.observe_pending_outcomes({"id": 1}))
    assert out == {"pending_found": 2, "observed": 2, "failed": []}
    assert observe.await_count == 2
    for call, oid in zip(observe.await_args_list, ("o-1", "o-2")):
        assert call.args[1] == {
            "source_type": "prediction_outcome",
            "source_id": oid,
        }, "el payload jamás afirma más que la identidad del outcome"
    sql, args = conn.queries[0]
    assert args == ("ws-1", 25)
    for condition in (
        "evaluation_status IN ('hit', 'miss')",
        "signal.signal_subtype = 'observed'",
        "prediction_horizon_days BETWEEN 1 AND 3650",
        "evidence_pack_id",
        "provenance_status = 'verified'",
    ):
        assert condition in sql


def test_one_failure_never_stops_the_rest(monkeypatch):
    _patch_db(monkeypatch, [{"outcome_id": "a"}, {"outcome_id": "b"}, {"outcome_id": "c"}])

    async def observe(user, payload):
        if payload["source_id"] == "b":
            raise RuntimeError("authoritative calibration evidence unavailable")
        return {"ok": True}

    monkeypatch.setattr(ap.calibration_observation_service, "observe", observe)
    out = asyncio.run(ap.observe_pending_outcomes({"id": 1}))
    assert out["observed"] == 2
    assert out["failed"] == [
        {"outcome_id": "b", "reason": "authoritative calibration evidence unavailable"}
    ], "el fallo queda VISIBLE con razón — cero error silencioso"


def test_best_effort_never_raises(monkeypatch):
    async def boom(user, limit=25):
        raise RuntimeError("db down")

    monkeypatch.setattr(ap, "observe_pending_outcomes", boom)
    assert asyncio.run(ap.run_best_effort({"id": 1})) is None


def test_engine_wires_the_sweep_into_gold_refresh_only():
    src = (
        REPO_ROOT / "console" / "app" / "services" / "intelligence" / "engine.py"
    ).read_text(encoding="utf-8")
    block = src.split("calibration_autopilot_summary = None", 1)[1]
    assert 'if should_persist and run_mode == "gold_refresh":' in block.split(
        "return {", 1
    )[0], "el barrido corre en el ciclo autónomo persistido, no en dry-runs"
    assert "run_best_effort" in block.split("return {", 1)[0]
    assert '"calibration_autopilot": calibration_autopilot_summary' in src, (
        "el resumen viaja en el resultado del run — auditable"
    )
