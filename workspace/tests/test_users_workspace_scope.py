from __future__ import annotations

import importlib
import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("INTERNAL_API_KEY", "workspace-test-internal-key-aaaaaaaaaaaaaaaa")

main = importlib.import_module("app.main")


class _FakePool:
    def __init__(self):
        self.calls: list[tuple[str, tuple]] = []

    async def fetch(self, sql: str, *args):
        self.calls.append((sql, args))
        return [{"id": 7, "email": "alice@example.com", "name": "Alice", "role": "user"}]


def _request(user: dict):
    return SimpleNamespace(state=SimpleNamespace(user=user))


async def _fake_pg(pool: _FakePool) -> _FakePool:
    return pool


@pytest.mark.asyncio
async def test_api_users_list_is_scoped_to_active_workspace(monkeypatch):
    pool = _FakePool()
    monkeypatch.setattr(main, "pg", lambda: _fake_pg(pool))

    user = {
        "id": 7,
        "email": "alice@example.com",
        "role": "user",
        "active_workspace_id": "11111111-1111-1111-1111-111111111111",
    }
    out = await main.api_users_list(_request(user))

    assert out["users"][0]["email"] == "alice@example.com"
    sql, args = pool.calls[0]
    assert "JOIN user_workspace_roles" in sql
    assert "uwr.workspace_id = $1::uuid" in sql
    assert "WHERE is_active = TRUE ORDER BY email" not in sql
    assert args == ("11111111-1111-1111-1111-111111111111",)


@pytest.mark.asyncio
async def test_api_users_list_without_workspace_only_returns_self(monkeypatch):
    pool = _FakePool()
    monkeypatch.setattr(main, "pg", lambda: _fake_pg(pool))

    out = await main.api_users_list(_request({"id": 7, "email": "alice@example.com", "role": "user"}))

    assert out["users"][0]["id"] == 7
    sql, args = pool.calls[0]
    assert "WHERE id = $1 AND is_active = TRUE" in sql
    assert args == (7,)
