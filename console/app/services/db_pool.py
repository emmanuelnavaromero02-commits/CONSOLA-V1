from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import asyncpg


MAIN_POOL: "asyncpg.Pool | None" = None


def db_dsn() -> str:
    return (
        os.environ.get("DATABASE_URL", "")
        .replace("postgresql+psycopg2://", "postgresql://")
        .replace("postgres+psycopg2://", "postgresql://")
    )


async def get_db_pool() -> "asyncpg.Pool":
    global MAIN_POOL
    import asyncpg as _asyncpg

    if MAIN_POOL is None:
        dsn = db_dsn()
        if not dsn:
            raise RuntimeError("DATABASE_URL is not configured (console)")
        MAIN_POOL = await _asyncpg.create_pool(
            dsn, min_size=1, max_size=5, command_timeout=10
        )
    return MAIN_POOL


async def close_main_pool() -> None:
    global MAIN_POOL
    if MAIN_POOL is not None:
        await MAIN_POOL.close()
        MAIN_POOL = None


def reset_db_pool_for_tests() -> None:
    global MAIN_POOL
    MAIN_POOL = None
