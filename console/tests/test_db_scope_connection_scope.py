from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from app.services.db_scope import SET_SCOPE_SQL, scoped_db


class _Connection:
    def __init__(self, *, in_transaction: bool) -> None:
        self.events: list[str] = []
        self._in_transaction = in_transaction

    def is_in_transaction(self) -> bool:
        return self._in_transaction

    @asynccontextmanager
    async def transaction(self):
        self.events.append("begin")
        yield
        self.events.append("commit")

    async def execute(self, sql: str, *args):
        self.events.append("scope" if sql == SET_SCOPE_SQL else "query")


@pytest.mark.asyncio
async def test_bare_connection_scope_lives_inside_its_own_transaction():
    conn = _Connection(in_transaction=False)
    async with scoped_db(conn, "tenant-a", "workspace-a") as scoped:
        await scoped.execute("SELECT 1")
    assert conn.events == ["begin", "scope", "query", "commit"]


@pytest.mark.asyncio
async def test_connection_already_in_a_transaction_keeps_the_callers_transaction():
    conn = _Connection(in_transaction=True)
    async with scoped_db(conn, "tenant-a", "workspace-a") as scoped:
        await scoped.execute("SELECT 1")
    assert conn.events == ["scope", "query"]
