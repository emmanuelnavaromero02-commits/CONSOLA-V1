from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from app.services import db_pool


@pytest.fixture(autouse=True)
def reset_pool():
    db_pool.reset_db_pool_for_tests()
    yield
    db_pool.reset_db_pool_for_tests()


def test_db_dsn_normalizes_sqlalchemy_postgres_scheme(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg2://user:pass@localhost:5432/app",
    )

    assert db_pool.db_dsn() == "postgresql://user:pass@localhost:5432/app"


@pytest.mark.asyncio
async def test_get_db_pool_requires_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(RuntimeError, match="DATABASE_URL is not configured"):
        await db_pool.get_db_pool()


@pytest.mark.asyncio
async def test_get_db_pool_creates_and_reuses_pool(monkeypatch):
    created: list[tuple[str, dict[str, int]]] = []
    fake_pool = object()

    async def create_pool(dsn: str, **kwargs):
        created.append((dsn, kwargs))
        return fake_pool

    monkeypatch.setenv("DATABASE_URL", "postgres+psycopg2://user:pass@db/app")
    monkeypatch.setitem(
        sys.modules,
        "asyncpg",
        SimpleNamespace(create_pool=create_pool),
    )

    first = await db_pool.get_db_pool()
    second = await db_pool.get_db_pool()

    assert first is fake_pool
    assert second is fake_pool
    assert created == [
        (
            "postgresql://user:pass@db/app",
            {"min_size": 1, "max_size": 5, "command_timeout": 10},
        )
    ]


@pytest.mark.asyncio
async def test_close_main_pool_closes_and_resets():
    class FakePool:
        closed = False

        async def close(self):
            self.closed = True

    fake_pool = FakePool()
    db_pool.MAIN_POOL = fake_pool

    await db_pool.close_main_pool()

    assert fake_pool.closed is True
    assert db_pool.MAIN_POOL is None
