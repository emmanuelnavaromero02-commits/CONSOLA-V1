from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.services import agent_scheduler


class _MissingTablePool:
    async def fetchval(self, *_args):
        return None


class _Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class _Acquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *_args):
        return False


class _MissingReservationConnection:
    def transaction(self):
        return _Transaction()

    async def execute(self, *_args):
        return "UPDATE 0"

    async def fetchrow(self, *_args):
        return None


class _PresentTablePool:
    def __init__(self):
        self.conn = _MissingReservationConnection()

    async def fetchval(self, *_args):
        return "agent_schedule_runs"

    def acquire(self):
        return _Acquire(self.conn)


@pytest.mark.asyncio
async def test_reservation_fails_closed_when_durable_table_is_missing(monkeypatch):
    async def pool():
        return _MissingTablePool()

    monkeypatch.setattr(agent_scheduler.auth, "pool", pool)
    with pytest.raises(RuntimeError, match="idempotency"):
        await agent_scheduler.reserve_scheduled_run(
            agent_id="agent-a",
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            scheduled_fire_at=datetime.now(timezone.utc),
        )


@pytest.mark.asyncio
async def test_finish_fails_closed_when_durable_table_is_missing(monkeypatch):
    async def pool():
        return _MissingTablePool()

    monkeypatch.setattr(agent_scheduler.auth, "pool", pool)
    with pytest.raises(RuntimeError, match="idempotency"):
        await agent_scheduler.finish_scheduled_run(
            schedule_run_id=1,
            agent_run_id=None,
            status="error",
            tenant_id="tenant-a",
            workspace_id="workspace-a",
        )


@pytest.mark.asyncio
async def test_reservation_fails_if_conflict_row_is_not_visible(monkeypatch):
    async def pool():
        return _PresentTablePool()

    monkeypatch.setattr(agent_scheduler.auth, "pool", pool)
    with pytest.raises(RuntimeError, match="reservation"):
        await agent_scheduler.reserve_scheduled_run(
            agent_id="00000000-0000-0000-0000-000000000001",
            tenant_id="00000000-0000-0000-0000-000000000002",
            workspace_id="00000000-0000-0000-0000-000000000003",
            scheduled_fire_at=datetime.now(timezone.utc),
        )


@pytest.mark.asyncio
async def test_finish_fails_if_reserved_row_is_not_updated(monkeypatch):
    async def pool():
        return _PresentTablePool()

    monkeypatch.setattr(agent_scheduler.auth, "pool", pool)
    with pytest.raises(RuntimeError, match="reservation"):
        await agent_scheduler.finish_scheduled_run(
            schedule_run_id=1,
            agent_run_id=None,
            status="error",
            tenant_id="00000000-0000-0000-0000-000000000002",
            workspace_id="00000000-0000-0000-0000-000000000003",
        )
