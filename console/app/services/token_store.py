"""Token usage tracking — records per-call LLM usage to Postgres."""
from __future__ import annotations

import os
import asyncpg

DATABASE_URL = os.environ.get("DATABASE_URL", "")

# Public pricing per 1M tokens (USD)
_COST_PER_1M: dict[str, dict[str, float]] = {
    "claude-haiku-4-5-20251001": {"input": 0.80,  "output": 4.00},
    "claude-sonnet-4-6":          {"input": 3.00,  "output": 15.00},
    "claude-opus-4-6":            {"input": 15.00, "output": 75.00},
}

_pool: asyncpg.Pool | None = None


def _is_platform_admin_context(user_context: dict | None) -> bool:
    role = str((user_context or {}).get("role") or "").strip()
    return role in {"owner", "super_admin", "admin"}


def _scope_from_context(user_context: dict | None) -> tuple[int | None, str | None, str | None]:
    if not user_context:
        return None, None, None
    raw_user_id = user_context.get("id")
    try:
        user_id = int(raw_user_id) if raw_user_id is not None else None
    except (TypeError, ValueError):
        user_id = None
    tenant_id = str(
        user_context.get("active_tenant_id") or user_context.get("tenant_id") or ""
    ).strip() or None
    workspace_id = str(
        user_context.get("active_workspace_id") or user_context.get("workspace_id") or ""
    ).strip() or None
    return user_id, tenant_id, workspace_id


async def _get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        dsn = DATABASE_URL.replace("postgresql+psycopg2://", "postgresql://")
        if not dsn:
            raise RuntimeError("DATABASE_URL is not configured (token_store)")
        _pool = await asyncpg.create_pool(dsn, min_size=1, max_size=3, command_timeout=10)
    return _pool


async def _set_db_scope(conn: asyncpg.Connection, tenant_id: str | None, workspace_id: str | None) -> None:
    if tenant_id and workspace_id:
        await conn.execute(
            "SELECT set_config('app.tenant_id', $1, true), set_config('app.workspace_id', $2, true)",
            tenant_id,
            workspace_id,
        )


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def record(
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_creation_tokens: int = 0,
    cache_read_tokens: int = 0,
    user_context: dict | None = None,
) -> None:
    """Persist one LLM call's token usage. Never raises — non-critical path."""
    try:
        pool = await _get_pool()
        user_id, tenant_id, workspace_id = _scope_from_context(user_context)
        async with pool.acquire() as conn:
            async with conn.transaction():
                await _set_db_scope(conn, tenant_id, workspace_id)
                await conn.execute(
                    "INSERT INTO token_usage "
                    "(provider, model, input_tokens, output_tokens, cache_creation_tokens, cache_read_tokens, "
                    "user_id, tenant_id, workspace_id) "
                    "VALUES ($1, $2, $3, $4, $5, $6, $7, $8::uuid, $9::uuid)",
                    provider, model, input_tokens, output_tokens, cache_creation_tokens, cache_read_tokens,
                    user_id, tenant_id, workspace_id,
                )
    except Exception:
        pass


async def summary(user_context: dict | None = None) -> dict:
    """Return accumulated totals grouped by model, with cost estimate."""
    try:
        pool = await _get_pool()
        _user_id, tenant_id, workspace_id = _scope_from_context(user_context)
        where_clause = ""
        args: tuple = ()
        if user_context and not _is_platform_admin_context(user_context):
            if not tenant_id or not workspace_id:
                return {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cache_creation_tokens": 0,
                    "cache_read_tokens": 0,
                    "calls": 0,
                    "cost_usd": 0.0,
                    "models": [],
                }
            where_clause = "WHERE tenant_id = $1::uuid AND workspace_id = $2::uuid"
            args = (tenant_id, workspace_id)
        async with pool.acquire() as conn:
            async with conn.transaction():
                await _set_db_scope(conn, tenant_id, workspace_id)
                rows = await conn.fetch(f"""
                    SELECT model,
                           SUM(input_tokens)::int           AS input_tokens,
                           SUM(output_tokens)::int          AS output_tokens,
                           SUM(cache_creation_tokens)::int  AS cache_creation_tokens,
                           SUM(cache_read_tokens)::int      AS cache_read_tokens,
                           COUNT(*)::int                    AS calls
                    FROM token_usage
                    {where_clause}
                    GROUP BY model
                    ORDER BY model
                """, *args)

        total_in = total_out = total_calls = 0
        total_cache_create = total_cache_read = 0
        total_cost = 0.0
        models = []

        for r in rows:
            m = dict(r)
            rates  = _COST_PER_1M.get(m["model"], {"input": 0.0, "output": 0.0})
            # Anthropic pricing: cache write = 1.25x input, cache read = 0.1x input
            cost = (
                m["input_tokens"]          / 1_000_000 * rates["input"]
                + m["output_tokens"]       / 1_000_000 * rates["output"]
                + m["cache_creation_tokens"] / 1_000_000 * rates["input"] * 1.25
                + m["cache_read_tokens"]     / 1_000_000 * rates["input"] * 0.10
            )
            m["cost_usd"] = round(cost, 6)
            total_in           += m["input_tokens"]
            total_out          += m["output_tokens"]
            total_cache_create += m["cache_creation_tokens"]
            total_cache_read   += m["cache_read_tokens"]
            total_calls        += m["calls"]
            total_cost         += cost
            models.append(m)

        return {
            "input_tokens":          total_in,
            "output_tokens":         total_out,
            "cache_creation_tokens": total_cache_create,
            "cache_read_tokens":     total_cache_read,
            "calls":                 total_calls,
            "cost_usd":              round(total_cost, 6),
            "models":                models,
        }
    except Exception:
        return {
            "input_tokens": 0, "output_tokens": 0,
            "cache_creation_tokens": 0, "cache_read_tokens": 0,
            "calls": 0, "cost_usd": 0.0, "models": [],
        }
