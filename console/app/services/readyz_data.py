from __future__ import annotations

import logging
import os
import re
from collections.abc import Mapping
from typing import Any, Callable


logger = logging.getLogger("app.main")
SAFE_GOLD_TABLE_RE = re.compile(r"gold_[A-Za-z0-9_]+")


def gold_readiness_dsn(environ: Mapping[str, str] = os.environ) -> str:
    raw = environ.get("GOLD_DATABASE_URL") or environ.get("DATABASE_URL", "")
    return raw.replace("postgresql+psycopg2://", "postgresql://")


async def default_gold_pool_factory(dsn: str):
    import asyncpg as _asyncpg

    return await _asyncpg.create_pool(dsn, min_size=1, max_size=1, command_timeout=5)


async def control_room_data_check(
    *,
    require_data: bool = False,
    db_pool_factory: Callable[[], Any],
    gold_pool_factory: Callable[[str], Any] = default_gold_pool_factory,
    environ: Mapping[str, str] = os.environ,
    log: logging.Logger = logger,
) -> dict[str, Any]:
    if not require_data:
        return {
            "status": "up",
            "required": False,
            "reason": "data_check_not_required",
        }

    operational_items = 0
    gold_tables = 0
    gold_rows = 0
    lineage_gold_rows = 0
    try:
        pool = await db_pool_factory()
        async with pool.acquire() as conn:
            operational_items = int(
                await conn.fetchval(
                    """
                SELECT COUNT(*)
                  FROM control_room_items
                 WHERE COALESCE(item_kind, '') <> 'source_state'
                """
                )
                or 0
            )
            lineage_gold_rows = int(
                await conn.fetchval(
                    """
                SELECT COALESCE(SUM(row_count), 0)::bigint
                  FROM (
                    SELECT DISTINCT ON (cartridge_id, silver_name)
                           COALESCE(row_count, 0)::bigint AS row_count
                      FROM silver_lineage
                     WHERE layer = 'gold'
                       AND COALESCE(row_count, 0) > 0
                     ORDER BY cartridge_id, silver_name, created_at DESC
                  ) latest_gold
                """
                )
                or 0
            )
    except Exception as exc:
        log.warning("readiness probe failed for control_room_data", exc_info=True)
        return {
            "status": "degraded",
            "required": require_data,
            "operational_items": 0,
            "error": type(exc).__name__,
        }

    gold_error = ""
    gold_dsn = gold_readiness_dsn(environ)
    if gold_dsn:
        try:
            gold_pool = await gold_pool_factory(gold_dsn)
            try:
                async with gold_pool.acquire() as gold_conn:
                    rows = await gold_conn.fetch(
                        """
                        SELECT tablename
                          FROM pg_tables
                         WHERE schemaname = 'public'
                           AND tablename LIKE 'gold\\_%' ESCAPE '\\'
                         ORDER BY tablename
                        """
                    )
                    gold_tables = len(rows)
                    for row in rows:
                        table_name = str(row["tablename"])
                        if not SAFE_GOLD_TABLE_RE.fullmatch(table_name):
                            continue
                        gold_rows += int(
                            await gold_conn.fetchval(
                                f'SELECT COUNT(*) FROM public."{table_name}"'
                            )
                            or 0
                        )
            finally:
                await gold_pool.close()
        except Exception as exc:
            log.warning("readiness probe failed for gold_data", exc_info=True)
            gold_error = type(exc).__name__

    has_data = operational_items > 0 or gold_rows > 0 or lineage_gold_rows > 0
    return {
        "status": "up" if has_data else "degraded",
        "required": require_data,
        "operational_items": operational_items,
        "gold_tables": gold_tables,
        "gold_rows": gold_rows,
        "lineage_gold_rows": lineage_gold_rows,
        "reason": "" if has_data else "no_control_room_or_gold_data",
        **({"gold_error": gold_error} if gold_error else {}),
    }
