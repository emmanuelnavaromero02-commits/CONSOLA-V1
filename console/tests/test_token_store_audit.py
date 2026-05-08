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
    assert "VALUES ($1, $2, $3, $4, $5, $6)" in source
