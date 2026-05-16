"""Sprint v1.41.1 — /api/freshness wiring tests.

These are *unit* tests against the router in isolation. The endpoint
needs the asyncpg pool, so we mount it on a bare FastAPI app, override
require_authenticated to bypass session/JWT setup, and patch
``auth.pool()`` with a tiny double that mimics the asyncpg interface
the router uses. A live-stack version of these calls lives in
tests/test_e2e_full_flow.py.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


_SIBLINGS = ("/cartridges/", "/refinement", "/vault", "/workspace", "/mcp-infra")


@pytest.fixture
def freshness_module():
    repo = Path(__file__).resolve().parents[1]
    sys.path[:] = [p for p in sys.path if not any(s in p for s in _SIBLINGS)]
    sys.path.insert(0, str(repo / "console"))
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    from app.routers import freshness as mod
    return mod


def _make_app(mod, fetch_rows):
    """Wrap the router with: require_authenticated bypassed, pool patched."""
    from app.dependencies import require_authenticated

    class _FakeConn:
        async def fetch(self, *_args, **_kwargs):
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
    api.dependency_overrides[require_authenticated] = lambda: {
        "id": 1, "email": "admin@example.com", "role": "admin",
    }
    return api


def test_freshness_unknown_cartridge_returns_404(freshness_module):
    app = _make_app(freshness_module, [])
    r = TestClient(app).get("/api/freshness/nope")
    assert r.status_code == 404


def test_freshness_returns_entity_rows(freshness_module):
    rows = [
        {"entity": "users", "watermark_value": "2026-05-10T00:00:00Z",
         "watermark_updated_at": None, "last_run_status": "success",
         "last_run_at": None, "age_seconds": 3600},
        {"entity": "timesheets", "watermark_value": None,
         "watermark_updated_at": None, "last_run_status": None,
         "last_run_at": None, "age_seconds": None},
    ]
    app = _make_app(freshness_module, rows)
    r = TestClient(app).get("/api/freshness/replicon")
    assert r.status_code == 200
    body = r.json()
    assert body["cartridge"] == "replicon"
    assert len(body["entities"]) == 2
    assert body["entities"][0]["entity"] == "users"
    assert body["entities"][0]["age_seconds"] == 3600


def test_freshness_summary_all_cartridges(freshness_module):
    rows = [
        {"cartridge_id": "replicon",  "entity_count": 5,
         "oldest_age_seconds": 86400, "newest_age_seconds": 60},
        {"cartridge_id": "sap_hcm",   "entity_count": 12,
         "oldest_age_seconds": 7200,  "newest_age_seconds": 300},
    ]
    app = _make_app(freshness_module, rows)
    r = TestClient(app).get("/api/freshness")
    assert r.status_code == 200
    body = r.json()
    assert len(body["cartridges"]) == 2
    ids = {c["cartridge_id"] for c in body["cartridges"]}
    assert ids == {"replicon", "sap_hcm"}


def test_freshness_requires_auth(freshness_module):
    """When dependency_overrides is NOT installed, require_authenticated
    must reject the request — confirms the router is gated."""
    api = FastAPI()
    api.include_router(freshness_module.router)
    r = TestClient(api).get("/api/freshness/replicon")
    # FastAPI raises HTTPException(401) which TestClient surfaces as 401.
    # Some auth backends return 403 if the cookie is present but invalid.
    assert r.status_code in (401, 403)


def test_freshness_router_registered_in_main():
    """main.py must include the new freshness router."""
    main_src = (Path(__file__).resolve().parents[1]
                / "console" / "app" / "main.py").read_text(encoding="utf-8")
    assert "freshness_router" in main_src
    assert "app.include_router(freshness_router.router)" in main_src


def test_freshness_sql_uses_real_column_names():
    """Guard rail: the v1.41.1 plan SQL used column names that don't
    exist in 00_schema.sql (entity_name on entity_config, ended_at on
    extraction_runs). Pin the corrected names so a future refactor
    can't silently drift back."""
    import re
    src = (Path(__file__).resolve().parents[1] / "console" / "app"
           / "routers" / "freshness.py").read_text(encoding="utf-8")
    # entity_config exposes `entity`, not `entity_name`
    assert "ec.entity" in src
    # extraction_runs uses `finished_at`, not `ended_at`.
    # Pull each conn.fetch("""…""") block and assert against the SQL only,
    # so the planning-history docstring doesn't pollute the match.
    sql_blocks = re.findall(r'conn\.fetch\(\s*"""(.+?)"""', src, re.DOTALL)
    assert sql_blocks, "expected SQL fetch() blocks in freshness.py"
    for sql in sql_blocks:
        assert "ended_at" not in sql, f"SQL should not reference ended_at:\n{sql}"
    # At least one block must hit extraction_runs.finished_at (the per-
    # entity detail query); the summary block only joins watermarks.
    assert any("finished_at" in sql for sql in sql_blocks), \
        "expected at least one SQL block to use extraction_runs.finished_at"
