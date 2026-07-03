from __future__ import annotations

import pytest

from app.services import readyz_data


class _Acquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *args):
        return None


class _Pool:
    def __init__(self, conn):
        self.conn = conn
        self.closed = False

    def acquire(self):
        return _Acquire(self.conn)

    async def close(self):
        self.closed = True


class _OperationalConn:
    def __init__(self, values: list[int]):
        self.values = list(values)

    async def fetchval(self, *_args):
        return self.values.pop(0)


class _GoldConn:
    def __init__(self, tables: list[str], counts: list[int]):
        self.tables = tables
        self.counts = list(counts)

    async def fetch(self, *_args):
        return [{"tablename": table} for table in self.tables]

    async def fetchval(self, *_args):
        return self.counts.pop(0)


@pytest.mark.asyncio
async def test_control_room_data_check_skips_data_checks_when_not_required():
    async def _blocked_pool():
        raise AssertionError("db should not be touched")

    assert await readyz_data.control_room_data_check(
        require_data=False,
        db_pool_factory=_blocked_pool,
    ) == {
        "status": "up",
        "required": False,
        "reason": "data_check_not_required",
    }


@pytest.mark.asyncio
async def test_control_room_data_check_reports_operational_data_ready():
    async def _pool():
        return _Pool(_OperationalConn([3, 0]))

    result = await readyz_data.control_room_data_check(
        require_data=True,
        db_pool_factory=_pool,
        environ={},
    )

    assert result == {
        "status": "up",
        "required": True,
        "operational_items": 3,
        "gold_tables": 0,
        "gold_rows": 0,
        "lineage_gold_rows": 0,
        "reason": "",
    }


@pytest.mark.asyncio
async def test_control_room_data_check_counts_safe_gold_tables_only():
    async def _pool():
        return _Pool(_OperationalConn([0, 0]))

    async def _gold_pool(_dsn: str):
        return _Pool(_GoldConn(["gold_safe", "bad_table"], [7]))

    result = await readyz_data.control_room_data_check(
        require_data=True,
        db_pool_factory=_pool,
        gold_pool_factory=_gold_pool,
        environ={"GOLD_DATABASE_URL": "postgresql+psycopg2://gold"},
    )

    assert result == {
        "status": "up",
        "required": True,
        "operational_items": 0,
        "gold_tables": 2,
        "gold_rows": 7,
        "lineage_gold_rows": 0,
        "reason": "",
    }


@pytest.mark.asyncio
async def test_control_room_data_check_degrades_when_operational_db_fails():
    async def _pool():
        raise RuntimeError("db unavailable")

    result = await readyz_data.control_room_data_check(
        require_data=True,
        db_pool_factory=_pool,
    )

    assert result == {
        "status": "degraded",
        "required": True,
        "operational_items": 0,
        "error": "RuntimeError",
    }
