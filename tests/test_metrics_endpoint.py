"""Sprint v1.41.1 — /api/metrics/operational unit tests.

Patches the asyncpg pool with a tiny double; verifies shape, types
and gating. Live-stack assertion lives in test_e2e_full_flow if we
add it later.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


_SIBLINGS = ("/cartridges/", "/refinement", "/vault", "/workspace", "/mcp-infra")


@pytest.fixture
def metrics_module():
    repo = Path(__file__).resolve().parents[1]
    sys.path[:] = [p for p in sys.path if not any(s in p for s in _SIBLINGS)]
    sys.path.insert(0, str(repo / "console"))
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    from app.routers import metrics as mod
    return mod


def _make_app(mod, fetchval_seq, fetch_rows):
    """fetchval_seq drives the fetchval() calls in order; fetch_rows
    is returned by fetch() (slowest_entities_7d)."""
    seq = list(fetchval_seq)

    class _FakeConn:
        async def fetchval(self, *_a, **_kw):
            return seq.pop(0) if seq else None
        async def fetch(self, *_a, **_kw):
            return fetch_rows

    class _Acquire:
        async def __aenter__(self):
            return _FakeConn()
        async def __aexit__(self, *_):
            return False

    class _FakePool:
        def acquire(self):
            return _Acquire()

    mod.auth.pool = AsyncMock(return_value=_FakePool())

    api = FastAPI()
    api.include_router(mod.router)
    api.dependency_overrides[mod.require_operations_read] = lambda: {
        "id": 1, "email": "admin@example.com", "role": "admin",
    }
    return api


def test_metrics_shape_and_types(metrics_module):
    rows = [
        {"cartridge_id": "replicon", "entity_name": "users",      "avg_sec": 12.5},
        {"cartridge_id": "sap_hcm",  "entity_name": "pa0001",     "avg_sec": 60.0},
    ]
    # fetchval order in metrics.py: extraction counters, audit counter,
    # control-room writeback counters, jobs, LLM usage.
    app = _make_app(metrics_module, [42, 3, 17.4, 250, 8, 2, 1, 14, 2, 9000, 1], rows)
    r = TestClient(app).get("/api/metrics/operational")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["extractions_24h"]      == 42
    assert body["errors_24h"]           == 3
    assert body["avg_duration_seconds"] == pytest.approx(17.4)
    assert body["audit_events_24h"]     == 250
    assert body["control_room"]["action_executions_24h"] == 8
    assert body["control_room"]["external_writebacks_24h"] == 2
    assert body["control_room"]["writeback_failures_24h"] == 1
    assert body["jobs"]["total_24h"] == 14
    assert body["jobs"]["failed_24h"] == 2
    assert body["llm"]["provider"]
    assert body["llm"]["tokens_24h"] == 9000
    assert body["llm"]["errors_24h"] == 1
    assert body["backup"]["status"] == "not_configured"
    assert isinstance(body["slowest_entities_7d"], list)
    assert len(body["slowest_entities_7d"]) == 2
    assert body["slowest_entities_7d"][0]["cartridge_id"] == "replicon"


def test_metrics_returns_zeros_when_empty(metrics_module):
    """Fresh install (no extractions yet) must not crash with None."""
    app = _make_app(metrics_module, [None, None, None, None], [])
    r = TestClient(app).get("/api/metrics/operational")
    assert r.status_code == 200
    body = r.json()
    assert body["extractions_24h"]      == 0
    assert body["errors_24h"]           == 0
    assert body["avg_duration_seconds"] == 0.0
    assert body["audit_events_24h"]     == 0
    assert body["slowest_entities_7d"]  == []
    assert body["control_room"]["action_executions_24h"] == 0
    assert body["jobs"]["failed_24h"] == 0
    assert body["llm"]["tokens_24h"] == 0


def test_metrics_requires_auth(metrics_module):
    api = FastAPI()
    api.include_router(metrics_module.router)
    r = TestClient(api).get("/api/metrics/operational")
    assert r.status_code in (401, 403)


def test_metrics_router_registered_in_main():
    main_src = (Path(__file__).resolve().parents[1]
                / "console" / "app" / "main.py").read_text(encoding="utf-8")
    assert "metrics_router" in main_src
    assert "app.include_router(metrics_router.router)" in main_src


def test_metrics_requires_operations_read_permission(metrics_module):
    src = Path(metrics_module.__file__).read_text(encoding="utf-8")
    assert 'require_permission("operations.read")' in src
    assert "Depends(require_authenticated)" not in src
