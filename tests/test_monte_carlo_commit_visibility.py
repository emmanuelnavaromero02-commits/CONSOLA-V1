from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.services.intelligence import monte_carlo_repository


class _Connection:
    def __init__(self, row):
        self.row = row
        self.calls = []

    def transaction(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, sql, *params):
        self.calls.append(("execute", sql, params))

    async def fetchrow(self, sql, *params):
        self.calls.append(("fetchrow", sql, params))
        return self.row


class _Acquire:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Pool:
    def __init__(self, row):
        self.connection = _Connection(row)

    def acquire(self):
        return _Acquire(self.connection)


USER = {
    "tenant_id": "tenant-a",
    "active_workspace_id": "workspace-a",
}


@pytest.mark.asyncio
async def test_require_visible_reads_simulation_after_commit():
    pool = _Pool({"simulation_id": "mc-visible"})

    row = await monte_carlo_repository.require_visible(pool, USER, "mc-visible")

    assert row["simulation_id"] == "mc-visible"
    fetch = [call for call in pool.connection.calls if call[0] == "fetchrow"]
    assert fetch[0][2] == ("workspace-a", "mc-visible")


@pytest.mark.asyncio
async def test_require_visible_fails_instead_of_returning_uncommitted_id():
    pool = _Pool(None)

    with pytest.raises(HTTPException) as exc:
        await monte_carlo_repository.require_visible(pool, USER, "mc-pending")

    assert exc.value.status_code == 503
    assert "commit was not visible" in str(exc.value.detail)
