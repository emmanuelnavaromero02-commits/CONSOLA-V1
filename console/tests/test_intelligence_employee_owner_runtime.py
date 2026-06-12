from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.services.intelligence import persistence


class _FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeConnection:
    def __init__(self):
        self.executed: list[tuple[str, tuple]] = []
        self.fetches: list[tuple[str, tuple]] = []

    def transaction(self):
        return _FakeTransaction()

    async def execute(self, sql: str, *params):
        self.executed.append((sql, params))
        return "OK"

    async def fetch(self, sql: str, *params):
        self.fetches.append((sql, params))
        return []


class _FakeAcquire:
    def __init__(self, conn: _FakeConnection):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakePool:
    def __init__(self, conn: _FakeConnection):
        self.conn = conn

    def acquire(self):
        return _FakeAcquire(self.conn)


def _user(**overrides):
    data = {
        "id": 101,
        "role": "user",
        "workspace_role": "workspace_user",
        "tenant_id": "11111111-1111-1111-1111-111111111111",
        "workspace_id": "22222222-2222-2222-2222-222222222222",
    }
    data.update(overrides)
    return data


@pytest.mark.asyncio
async def test_employee_signal_list_is_owner_scoped(monkeypatch):
    conn = _FakeConnection()
    monkeypatch.setattr(persistence.auth, "pool", AsyncMock(return_value=_FakePool(conn)))

    result = await persistence.list_signals(_user(id=101), limit=25)

    assert result == {"signals": []}
    assert conn.executed[0][0].startswith("SELECT set_config")
    sql, params = conn.fetches[0]
    assert "tenant_id::text" in sql
    assert "owner_user_id" in sql
    assert params == (
        "22222222-2222-2222-2222-222222222222",
        "11111111-1111-1111-1111-111111111111",
        101,
        25,
    )


@pytest.mark.asyncio
async def test_workspace_admin_signal_list_is_workspace_scoped(monkeypatch):
    conn = _FakeConnection()
    monkeypatch.setattr(persistence.auth, "pool", AsyncMock(return_value=_FakePool(conn)))

    result = await persistence.list_signals(_user(id=201, workspace_role="workspace_admin"), limit=25)

    assert result == {"signals": []}
    sql, params = conn.fetches[0]
    assert "tenant_id::text" in sql
    assert "owner_user_id" not in sql
    assert params == (
        "22222222-2222-2222-2222-222222222222",
        "11111111-1111-1111-1111-111111111111",
        25,
    )
