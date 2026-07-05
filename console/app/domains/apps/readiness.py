"""Gold readiness checks for analytic apps."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from app.domains.apps.payloads import app_datasets_from_payload


WorkspaceScopeResolver = Callable[[dict | None], Awaitable[tuple[str, str]]]
GoldConnect = Callable[[str], Awaitable[Any]]
LoggerDebug = Callable[..., None]


async def default_gold_connect(dsn: str) -> Any:
    import asyncpg as _asyncpg

    return await _asyncpg.connect(dsn, command_timeout=5)


async def gold_ready_datasets_for_apps(
    user: dict | None,
    apps_payload: Any,
    *,
    dsn: str,
    workspace_scope_resolver: WorkspaceScopeResolver,
    connect_gold: GoldConnect = default_gold_connect,
    logger_debug: LoggerDebug | None = None,
) -> tuple[set[str] | None, str]:
    requested = app_datasets_from_payload(apps_payload)
    if not requested:
        return None, "no_dataset_metadata"
    if not dsn:
        return None, "gold_dsn_missing"
    tenant_id, workspace_id = await workspace_scope_resolver(user)
    if not workspace_id:
        return set(), "workspace_scope_missing"
    try:
        conn = await connect_gold(dsn)
    except Exception:
        if logger_debug:
            logger_debug("Failed to connect to Gold for app readiness", exc_info=True)
        return None, "gold_unreachable"
    ready: set[str] = set()
    try:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true), set_config('app.workspace_id', $2, true)",
                tenant_id or "",
                workspace_id,
            )
            for dataset in sorted(requested):
                table = f"gold_{dataset}"
                exists = bool(
                    await conn.fetchval("SELECT to_regclass($1)", f"public.{table}")
                )
                if not exists:
                    continue
                columns = {
                    str(row["column_name"])
                    for row in await conn.fetch(
                        """
                        SELECT column_name
                          FROM information_schema.columns
                         WHERE table_schema = 'public'
                           AND table_name = $1
                        """,
                        table,
                    )
                }
                if "workspace_id" not in columns:
                    continue
                if "tenant_id" in columns and tenant_id:
                    has_row = await conn.fetchval(
                        f'SELECT 1 FROM public."{table}" WHERE workspace_id::text = $1 AND tenant_id::text = $2 LIMIT 1',
                        workspace_id,
                        tenant_id,
                    )
                else:
                    has_row = await conn.fetchval(
                        f'SELECT 1 FROM public."{table}" WHERE workspace_id::text = $1 LIMIT 1',
                        workspace_id,
                    )
                if has_row:
                    ready.add(dataset)
    except Exception:
        if logger_debug:
            logger_debug("Failed to verify Gold readiness for apps", exc_info=True)
        return None, "gold_check_failed"
    finally:
        await conn.close()
    return ready, "checked"
