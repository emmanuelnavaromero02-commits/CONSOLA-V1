from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

from app.services.control_room import cycle_blackboard as bb

REPO_ROOT = Path(__file__).resolve().parents[1]


class _FakeConn:
    def __init__(self):
        self.calls = []

    async def fetch(self, sql, *args):
        self.calls.append((sql, args))
        if "pipeline_runs" in sql:
            return [{
                "run_id": "r1", "cartridge_id": "sap_successfactors",
                "entity": "gold_foundation", "status": "success",
                "started_at": datetime(2026, 8, 19, 3, 5, tzinfo=timezone.utc),
                "record_count": 7339, "transition": "gold_materialized",
                "target": "all",
            }]
        if "intelligence_signals" in sql:
            return [{"signal_id": "s1", "metric": "net_margin",
                     "severity": "high", "signal_subtype": "observed",
                     "created_at": datetime(2026, 8, 19, 3, 6, tzinfo=timezone.utc)}]
        if "control_room_lessons" in sql:
            return [{"item_id": "i1", "cartridge_id": "replicon",
                     "anomaly_type": "margin_drop", "rule": "escalar a supervisor",
                     "confidence": 0.9,
                     "created_at": datetime(2026, 8, 18, tzinfo=timezone.utc)}]
        if "calibration_observations" in sql:
            return [{"calibration_group": "replicon:net_margin",
                     "source_type": "prediction_outcome", "source_id": "o1",
                     "provenance_status": "verified",
                     "created_at": datetime(2026, 8, 19, tzinfo=timezone.utc)}]
        raise AssertionError("query inesperada")


class _FakeScope:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn, "tenant-1", "ws-1"

    async def __aexit__(self, *exc):
        return False


def test_composes_four_sections_scoped(monkeypatch):
    conn = _FakeConn()
    monkeypatch.setattr(bb.auth, "pool", AsyncMock(return_value=object()))
    monkeypatch.setattr(bb, "scoped_db_for_user", lambda pool, user: _FakeScope(conn))
    out = asyncio.run(bb.read_blackboard({"id": 1}, limit=5))
    assert out["workspace_id"] == "ws-1"
    assert [c["transition"] for c in out["cycles"]] == ["gold_materialized"]
    assert out["signals"][0]["metric"] == "net_margin"
    assert out["lessons"][0]["rule"] == "escalar a supervisor"
    assert out["calibration"][0]["provenance_status"] == "verified"
    assert out["cycles"][0]["started_at"].startswith("2026-08-19T03:05"), "JSON-safe"
    assert len(conn.calls) == 4
    for _sql, args in conn.calls:
        assert args == ("ws-1", 5), "todo scoped al workspace y con el mismo limite"


def test_limit_is_bounded(monkeypatch):
    conn = _FakeConn()
    monkeypatch.setattr(bb.auth, "pool", AsyncMock(return_value=object()))
    monkeypatch.setattr(bb, "scoped_db_for_user", lambda pool, user: _FakeScope(conn))
    asyncio.run(bb.read_blackboard({"id": 1}, limit=99999))
    assert conn.calls[0][1] == ("ws-1", 100)


def test_route_contract():
    src = (REPO_ROOT / "console" / "app" / "routers" / "control_room.py").read_text(
        encoding="utf-8"
    )
    block = src.split('"/blackboard"', 1)[1].split("@router.get", 1)[0]
    assert 'require_permission("operations.read")' in block
    assert "cycle_blackboard.read_blackboard" in block
