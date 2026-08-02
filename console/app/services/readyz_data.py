from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from typing import Any, Callable


logger = logging.getLogger("app.main")


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
    tenant_id = ""
    workspace_id = ""
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
            scope = await conn.fetchrow(
                "SELECT tenant_id::text,id::text AS workspace_id FROM workspaces "
                "ORDER BY created_at ASC NULLS LAST,id ASC LIMIT 1"
            )
            if scope:
                tenant_id = str(scope["tenant_id"] or "")
                workspace_id = str(scope["workspace_id"] or "")
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
                    async with gold_conn.transaction(
                        isolation="repeatable_read", readonly=True
                    ):
                        await gold_conn.execute(
                            "SELECT set_config('app.tenant_id',$1,true),"
                            "set_config('app.workspace_id',$2,true)",
                            tenant_id,
                            workspace_id,
                        )
                        row = await gold_conn.fetchrow(
                            """
                        SELECT count(*) FILTER (WHERE h.layer='gold') AS gold_tables,
                               COALESCE(sum(r.row_count) FILTER (WHERE h.layer='gold'),0) AS gold_rows,
                               COALESCE(sum(r.row_count) FILTER (WHERE h.layer='silver'),0) AS silver_rows
                          FROM omega_publication.dataset_publication_heads h
                          JOIN omega_publication.materialization_runs r
                            ON r.materialization_run_id=h.materialization_run_id
                         WHERE h.tenant_id::text=$1 AND h.workspace_id::text=$2
                           AND r.status IN ('published','legacy_unverified')
                        """,
                            tenant_id,
                            workspace_id,
                        )
                        gold_tables = int(row["gold_tables"] or 0) if row else 0
                        gold_rows = int(row["gold_rows"] or 0) if row else 0
                        lineage_gold_rows = int(row["silver_rows"] or 0) if row else 0
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
