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
async def test_token_store_summary_returns_zero_totals_when_db_fails(token_store_module, monkeypatch):
    class FailingPool:
        async def fetch(self, *args, **kwargs):
            raise RuntimeError("db down")

    async def fake_pool():
        return FailingPool()

    monkeypatch.setattr(token_store_module, "_get_pool", fake_pool)

    summary = await token_store_module.summary()

    assert summary == {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_tokens": 0,
        "cache_read_tokens": 0,
        "calls": 0,
        "cost_usd": 0.0,
        "models": [],
    }


@pytest.mark.anyio
async def test_token_store_summary_handles_empty_usage_table(token_store_module, monkeypatch):
    class EmptyPool:
        async def fetch(self, query):
            return []

    async def fake_pool():
        return EmptyPool()

    monkeypatch.setattr(token_store_module, "_get_pool", fake_pool)

    summary = await token_store_module.summary()

    assert summary["input_tokens"] == 0
    assert summary["output_tokens"] == 0
    assert summary["cache_creation_tokens"] == 0
    assert summary["cache_read_tokens"] == 0
    assert summary["calls"] == 0
    assert summary["cost_usd"] == 0.0
    assert summary["models"] == []


def test_token_store_sql_matches_token_usage_cache_schema(token_store_module):
    source = Path("console/app/services/token_store.py").read_text(encoding="utf-8")

    assert "cache_creation_tokens" in source
    assert "cache_read_tokens" in source
    assert "user_id, tenant_id, workspace_id" in source
    assert "VALUES ($1, $2, $3, $4, $5, $6, $7, $8::uuid, $9::uuid)" in source


@pytest.mark.anyio
async def test_token_store_summary_scopes_non_platform_users(token_store_module, monkeypatch):
    captured = {}

    class AsyncContext:
        def __init__(self, value):
            self.value = value

        async def __aenter__(self):
            return self.value

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class CaptureConn:
        def transaction(self):
            return AsyncContext(self)

        async def execute(self, query, *args):
            captured["scope_query"] = query
            captured["scope_args"] = args

        async def fetch(self, query, *args):
            captured["query"] = query
            captured["args"] = args
            return []

    class CapturePool:
        def acquire(self):
            return AsyncContext(CaptureConn())

    async def fake_pool():
        return CapturePool()

    monkeypatch.setattr(token_store_module, "_get_pool", fake_pool)

    await token_store_module.summary(user_context={
        "id": 42,
        "role": "user",
        "active_tenant_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "active_workspace_id": "11111111-1111-1111-1111-111111111111",
    })

    assert "set_config('app.tenant_id'" in captured["scope_query"]
    assert captured["scope_args"] == (
        "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "11111111-1111-1111-1111-111111111111",
    )
    assert "WHERE tenant_id = $1::uuid AND workspace_id = $2::uuid" in captured["query"]
    assert captured["args"] == (
        "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "11111111-1111-1111-1111-111111111111",
    )
