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
def token_store_module(monkeypatch):
    workspace_root = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(workspace_root))
    monkeypatch.setitem(sys.modules, "asyncpg", _module())
    sys.modules.pop("app.services.token_store", None)
    token_store = importlib.import_module("app.services.token_store")
    if workspace_root not in Path(token_store.__file__).parents:
        for name in [key for key in sys.modules if key == "app" or key.startswith("app.")]:
            sys.modules.pop(name, None)
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
    def __init__(self):
        self.executed: list[tuple[str, tuple]] = []

    def transaction(self):
        return AsyncContext(self)

    async def execute(self, query, *args):
        self.executed.append((query, args))


class FakePool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return AsyncContext(self.conn)


@pytest.mark.asyncio
async def test_workspace_record_persists_duration_and_surface(token_store_module, monkeypatch):
    conn = FakeConn()

    async def fake_pool():
        return FakePool(conn)

    monkeypatch.setattr(token_store_module, "_get_pool", fake_pool)

    await token_store_module.record(
        "anthropic", "claude-haiku-4-5-20251001", 3, 2,
        duration_ms=250, surface="workspace",
    )

    query, args = conn.executed[-1]
    assert "duration_ms, surface" in query
    assert args[-2:] == (250, "workspace")


@pytest.mark.asyncio
async def test_workspace_record_drops_unknown_surface_and_negative_duration(token_store_module, monkeypatch):
    conn = FakeConn()

    async def fake_pool():
        return FakePool(conn)

    monkeypatch.setattr(token_store_module, "_get_pool", fake_pool)

    await token_store_module.record(
        "anthropic", "claude-haiku-4-5-20251001", 3, 2,
        duration_ms=-1, surface="not-a-surface",
    )

    _query, args = conn.executed[-1]
    assert args[-2:] == (None, None)
