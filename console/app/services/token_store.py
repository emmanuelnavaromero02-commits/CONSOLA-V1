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
    # Gemini paid tier
    "gemini-2.5-flash-lite":      {"input": 0.10,  "output": 0.40},
    "gemini-2.5-flash":           {"input": 0.30,  "output": 2.50},
    "gemini-2.5-pro":             {"input": 1.25,  "output": 10.00},
    "gemini-2.0-flash":           {"input": 0.10,  "output": 0.40},
    "gemini-2.0-flash-lite":      {"input": 0.075, "output": 0.30},
}

_pool: asyncpg.Pool | None = None


async def _get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        dsn = DATABASE_URL.replace("postgresql+psycopg2://", "postgresql://")
        _pool = await asyncpg.create_pool(dsn, min_size=1, max_size=3)
    return _pool


async def record(
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_creation_tokens: int = 0,
    cache_read_tokens: int = 0,
) -> None:
    """Persist one LLM call's token usage. Never raises — non-critical path."""
    try:
        pool = await _get_pool()
        await pool.execute(
            "INSERT INTO token_usage "
            "(provider, model, input_tokens, output_tokens, cache_creation_tokens, cache_read_tokens) "
            "VALUES ($1, $2, $3, $4, $5, $6)",
            provider, model, input_tokens, output_tokens, cache_creation_tokens, cache_read_tokens,
        )
    except Exception:
        pass


async def summary() -> dict:
    """Return accumulated totals grouped by model, with cost estimate."""
    try:
        pool = await _get_pool()
        rows = await pool.fetch("""
            SELECT model,
                   SUM(input_tokens)::int           AS input_tokens,
                   SUM(output_tokens)::int          AS output_tokens,
                   SUM(cache_creation_tokens)::int  AS cache_creation_tokens,
                   SUM(cache_read_tokens)::int      AS cache_read_tokens,
                   COUNT(*)::int                    AS calls
            FROM token_usage
            GROUP BY model
            ORDER BY model
        """)

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
