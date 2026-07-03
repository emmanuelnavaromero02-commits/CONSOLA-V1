"""Database metadata helpers for data platform runtime checks."""

from __future__ import annotations

from typing import Any, Awaitable, Callable


_COLUMN_EXISTS_CACHE: dict[tuple[str, str], bool] = {}


async def table_has_column(
    table: str,
    column: str,
    *,
    pool_getter: Callable[[], Awaitable[Any]],
    logger_debug: Callable[..., None] | None = None,
    refresh: bool = False,
) -> bool:
    key = (table, column)
    if not refresh and key in _COLUMN_EXISTS_CACHE:
        return _COLUMN_EXISTS_CACHE[key]
    try:
        pool = await pool_getter()
        exists = await pool.fetchval(
            """
            SELECT EXISTS (
                SELECT 1
                  FROM information_schema.columns
                 WHERE table_schema='public'
                   AND table_name=$1
                   AND column_name=$2
            )
            """,
            table,
            column,
        )
        _COLUMN_EXISTS_CACHE[key] = bool(exists)
        return bool(exists)
    except Exception:
        if logger_debug:
            logger_debug("Could not inspect column %s.%s", table, column, exc_info=True)
        _COLUMN_EXISTS_CACHE[key] = False
        return False


def clear_table_metadata_cache() -> None:
    _COLUMN_EXISTS_CACHE.clear()
