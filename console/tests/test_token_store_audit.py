from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

import pytest


def _module(**attrs):
    mod = types.ModuleType("stub")
    for key, value in attrs.items():
        setattr(mod, key, value)
    return mod


@pytest.fixture()
def anyio_backend():
    return "asyncio"


@pytest.fixture()
def token_store_module(monkeypatch):
    monkeypatch.setitem(sys.modules, "asyncpg", _module())
    sys.modules.pop("app.services.token_store", None)
    token_store = importlib.import_module("app.services.token_store")
    yield token_store
    sys.modules.pop("app.services.token_store", None)


class AsyncContext:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeConn:
    def __init__(self, rows=None, fetchval_results=None):
        self.rows = rows or []
        self.fetchval_results = list(fetchval_results or [])
        self.executed: list[tuple[str, tuple]] = []
        self.fetches: list[tuple[str, tuple]] = []
        self.fetchvals: list[tuple[str, tuple]] = []

    def transaction(self):
        return AsyncContext(self)

    async def execute(self, query, *args):
        self.executed.append((query, args))

    async def fetch(self, query, *args):
        self.fetches.append((query, args))
        return self.rows

    async def fetchval(self, query, *args):
        self.fetchvals.append((query, args))
        return self.fetchval_results.pop(0) if self.fetchval_results else None


class FakePool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return AsyncContext(self.conn)


def _install_pool(monkeypatch, token_store_module, conn):
    async def fake_pool():
        return FakePool(conn)

    monkeypatch.setattr(token_store_module, "_get_pool", fake_pool)


WORKSPACE_USER = {
    "id": 42,
    "role": "user",
    "active_tenant_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    "active_workspace_id": "11111111-1111-1111-1111-111111111111",
}


@pytest.mark.anyio
async def test_token_store_record_never_raises_when_db_fails(token_store_module, monkeypatch):
    class FailingPool:
        async def execute(self, *args, **kwargs):
            raise RuntimeError("db down")

    async def fake_pool():
        return FailingPool()

    monkeypatch.setattr(token_store_module, "_get_pool", fake_pool)

    await token_store_module.record("anthropic", "claude-haiku-4-5-20251001", 10, 20)


@pytest.mark.anyio
async def test_token_store_record_persists_duration_and_surface(token_store_module, monkeypatch):
    conn = FakeConn()
    _install_pool(monkeypatch, token_store_module, conn)

    await token_store_module.record(
        "anthropic", "claude-haiku-4-5-20251001", 10, 20,
        duration_ms=1234, surface="copilot",
    )

    query, args = conn.executed[-1]
    assert "duration_ms, surface" in query
    assert args[-2:] == (1234, "copilot")


@pytest.mark.anyio
@pytest.mark.parametrize("surface", ["", "unknown", "COPILOT-injected", None])
async def test_token_store_record_rejects_unknown_surfaces(token_store_module, monkeypatch, surface):
    conn = FakeConn()
    _install_pool(monkeypatch, token_store_module, conn)

    await token_store_module.record(
        "anthropic", "claude-haiku-4-5-20251001", 1, 1,
        duration_ms=-5, surface=surface,
    )

    _query, args = conn.executed[-1]
    assert args[-2:] == (None, None)


@pytest.mark.anyio
async def test_token_store_summary_reports_unavailable_when_db_fails(token_store_module, monkeypatch):
    class FailingPool:
        async def fetch(self, *args, **kwargs):
            raise RuntimeError("db down")

    async def fake_pool():
        return FailingPool()

    monkeypatch.setattr(token_store_module, "_get_pool", fake_pool)

    summary = await token_store_module.summary()

    assert summary == {
        "available": False,
        "input_tokens": None,
        "output_tokens": None,
        "cache_creation_tokens": None,
        "cache_read_tokens": None,
        "calls": None,
        "cost_usd": None,
        "models": [],
        "unpriced_models": [],
        "avg_response_ms": None,
        "queries_count": None,
    }


@pytest.mark.anyio
async def test_token_store_summary_handles_empty_usage_table(token_store_module, monkeypatch):
    conn = FakeConn(rows=[], fetchval_results=[None, 0])
    _install_pool(monkeypatch, token_store_module, conn)

    summary = await token_store_module.summary()

    assert summary["available"] is True
    assert summary["input_tokens"] == 0
    assert summary["output_tokens"] == 0
    assert summary["cache_creation_tokens"] == 0
    assert summary["cache_read_tokens"] == 0
    assert summary["calls"] == 0
    assert summary["cost_usd"] == 0.0
    assert summary["models"] == []
    assert summary["unpriced_models"] == []
    assert summary["avg_response_ms"] is None
    assert summary["queries_count"] == 0


@pytest.mark.anyio
async def test_token_store_summary_excludes_unpriced_models_from_total(token_store_module, monkeypatch):
    rows = [
        {
            "model": "claude-haiku-4-5-20251001",
            "input_tokens": 1_000_000,
            "output_tokens": 0,
            "cache_creation_tokens": 0,
            "cache_read_tokens": 0,
            "calls": 3,
        },
        {
            "model": "mystery-model",
            "input_tokens": 5_000_000,
            "output_tokens": 5_000_000,
            "cache_creation_tokens": 0,
            "cache_read_tokens": 0,
            "calls": 2,
        },
    ]
    conn = FakeConn(rows=rows, fetchval_results=[250.0, 7])
    _install_pool(monkeypatch, token_store_module, conn)

    summary = await token_store_module.summary()

    assert summary["available"] is True
    assert summary["cost_usd"] == pytest.approx(0.80)
    assert summary["unpriced_models"] == ["mystery-model"]
    priced = {m["model"]: m for m in summary["models"]}
    assert priced["claude-haiku-4-5-20251001"]["priced"] is True
    assert priced["claude-haiku-4-5-20251001"]["cost_usd"] == pytest.approx(0.80)
    assert priced["mystery-model"]["priced"] is False
    assert priced["mystery-model"]["cost_usd"] is None
    assert summary["calls"] == 5
    assert summary["avg_response_ms"] == pytest.approx(250.0)
    assert summary["queries_count"] == 7


@pytest.mark.anyio
async def test_token_store_summary_latency_only_counts_copilot_surface(token_store_module, monkeypatch):
    conn = FakeConn(rows=[], fetchval_results=[None, 0])
    _install_pool(monkeypatch, token_store_module, conn)

    await token_store_module.summary()

    latency_query = conn.fetchvals[0][0]
    assert "AVG(duration_ms)" in latency_query
    assert "duration_ms IS NOT NULL" in latency_query
    assert "surface = 'copilot'" in latency_query


def test_token_store_sql_matches_token_usage_schema(token_store_module):
    source = Path("console/app/services/token_store.py").read_text(encoding="utf-8")

    assert "cache_creation_tokens" in source
    assert "cache_read_tokens" in source
    assert "user_id, tenant_id, workspace_id, duration_ms, surface" in source
    assert "VALUES ($1, $2, $3, $4, $5, $6, $7, $8::uuid, $9::uuid, $10, $11)" in source


@pytest.mark.anyio
async def test_token_store_summary_scopes_non_platform_users(token_store_module, monkeypatch):
    conn = FakeConn(rows=[], fetchval_results=[None, 4])
    _install_pool(monkeypatch, token_store_module, conn)

    summary = await token_store_module.summary(user_context=WORKSPACE_USER)

    scope_query, scope_args = conn.executed[0]
    assert "set_config('app.tenant_id'" in scope_query
    assert scope_args == (
        "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "11111111-1111-1111-1111-111111111111",
    )
    fetch_query, fetch_args = conn.fetches[0]
    assert "WHERE tenant_id = $1::uuid AND workspace_id = $2::uuid" in fetch_query
    assert fetch_args == (
        "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "11111111-1111-1111-1111-111111111111",
    )
    latency_query, latency_args = conn.fetchvals[0]
    assert "tenant_id = $1::uuid AND workspace_id = $2::uuid" in latency_query
    assert latency_args == fetch_args
    queries_query, queries_args = conn.fetchvals[1]
    assert "copilot.message.send" in queries_query
    assert "JOIN conversations" in queries_query
    assert queries_args == ("11111111-1111-1111-1111-111111111111",)
    assert summary["queries_count"] == 4


@pytest.mark.anyio
async def test_token_store_summary_platform_admin_counts_all_queries(token_store_module, monkeypatch):
    conn = FakeConn(rows=[], fetchval_results=[None, 99])
    _install_pool(monkeypatch, token_store_module, conn)

    summary = await token_store_module.summary(user_context={"id": 1, "role": "super_admin"})

    fetch_query, fetch_args = conn.fetches[0]
    assert "WHERE tenant_id" not in fetch_query
    assert fetch_args == ()
    queries_query, queries_args = conn.fetchvals[1]
    assert "copilot.message.send" in queries_query
    assert "JOIN conversations" not in queries_query
    assert queries_args == ()
    assert summary["queries_count"] == 99


@pytest.mark.anyio
async def test_token_store_summary_without_scope_returns_empty_but_available(token_store_module, monkeypatch):
    conn = FakeConn()
    _install_pool(monkeypatch, token_store_module, conn)

    summary = await token_store_module.summary(user_context={"id": 7, "role": "user"})

    assert summary["available"] is True
    assert summary["calls"] == 0
    assert summary["queries_count"] == 0
    assert summary["avg_response_ms"] is None
    assert conn.fetches == []
