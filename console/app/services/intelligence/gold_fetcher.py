from __future__ import annotations

import asyncio
import os
import re
import time
from copy import deepcopy
from typing import Any

import asyncpg
from fastapi import HTTPException

from app.services.gold_publication_relation import (
    published_relation_columns,
    resolve_published_gold_relation,
)
from app.services.intelligence.utils import workspace_scope


_SAFE_DATASET_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_CacheKey = tuple[str, str, str, int, str, int]
_GOLD_ROW_CACHE: dict[_CacheKey, tuple[float, list[dict[str, Any]]]] = {}
_GOLD_ROW_CACHE_LOCKS: dict[_CacheKey, asyncio.Lock] = {}


def _normalize_dsn(raw: str) -> str:
    return (raw or "").replace("postgresql+psycopg2://", "postgresql://")


def _gold_dsn() -> str:
    return _normalize_dsn(
        os.environ.get("GOLD_DATABASE_URL") or os.environ.get("DATABASE_URL") or ""
    )


def _gold_table(dataset: str) -> str:
    clean = str(dataset or "").strip()
    if not _SAFE_DATASET_RE.fullmatch(clean):
        raise HTTPException(400, "invalid intelligence dataset")
    return f"gold_{clean}"


def _gold_cache_ttl() -> float:
    raw = os.environ.get("OMEGA_GOLD_ROW_CACHE_TTL_SECONDS", "15")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 15.0
    return max(0.0, min(value, 300.0))


def _gold_cache_get(key: _CacheKey) -> list[dict[str, Any]] | None:
    ttl = _gold_cache_ttl()
    if ttl <= 0:
        return None
    cached = _GOLD_ROW_CACHE.get(key)
    if not cached:
        return None
    expires_at, rows = cached
    if expires_at <= time.monotonic():
        _GOLD_ROW_CACHE.pop(key, None)
        return None
    return deepcopy(rows)


def _gold_cache_set(key: _CacheKey, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ttl = _gold_cache_ttl()
    if ttl > 0:
        _GOLD_ROW_CACHE[key] = (time.monotonic() + ttl, deepcopy(rows))
    return rows


def clear_gold_row_cache(
    tenant_id: str | None = None, workspace_id: str | None = None
) -> None:
    """Clear cached Gold reads after sync/materialization updates."""
    tenant_text = str(tenant_id or "").strip()
    workspace_text = str(workspace_id or "").strip()
    if not tenant_text and not workspace_text:
        _GOLD_ROW_CACHE.clear()
        _GOLD_ROW_CACHE_LOCKS.clear()
        return

    def matches(key: _CacheKey) -> bool:
        _dataset, key_tenant, key_workspace, _limit, _run, _generation = key
        if tenant_text and key_tenant != tenant_text:
            return False
        if workspace_text and key_workspace != workspace_text:
            return False
        return True

    for key in list(_GOLD_ROW_CACHE):
        if matches(key):
            _GOLD_ROW_CACHE.pop(key, None)
    for key in list(_GOLD_ROW_CACHE_LOCKS):
        if matches(key):
            _GOLD_ROW_CACHE_LOCKS.pop(key, None)


async def query_gold_dataset_rows(
    dataset: str, user: dict | None, limit: int = 5000
) -> list[dict[str, Any]]:
    """Read workspace-scoped Gold rows directly for intelligence runs.

    Refinement can still serve datasets for legacy flows, but the intelligence
    readiness gate verifies Gold tables directly. This fetcher keeps the run path
    aligned with that gate and refuses unscoped Gold reads for authenticated users.
    """
    dsn = _gold_dsn()
    if not dsn:
        raise HTTPException(503, "gold database unavailable")
    _gold_table(dataset)
    tenant_id, workspace_id = workspace_scope(user)
    if not tenant_id:
        raise HTTPException(
            403, "gold dataset requires complete tenant/workspace scope"
        )
    safe_limit = max(1, min(int(limit or 5000), 5000))
    conn = await asyncpg.connect(dsn, command_timeout=10)
    try:
        async with conn.transaction(isolation="repeatable_read", readonly=True):
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true), set_config('app.workspace_id', $2, true)",
                tenant_id or "",
                workspace_id,
            )
            relation = await resolve_published_gold_relation(
                conn, tenant_id, workspace_id, dataset
            )
            materialization_run_id = relation.run_id
            head_generation = relation.generation
            cache_key = (
                str(dataset),
                str(tenant_id),
                str(workspace_id),
                safe_limit,
                materialization_run_id,
                head_generation,
            )
            cached = _gold_cache_get(cache_key)
            if cached is not None:
                return cached
            lock = _GOLD_ROW_CACHE_LOCKS.setdefault(cache_key, asyncio.Lock())
            async with lock:
                cached = _gold_cache_get(cache_key)
                if cached is not None:
                    return cached
                await conn.execute(
                    "SELECT set_config('app.tenant_id', $1, true), set_config('app.workspace_id', $2, true)",
                    tenant_id or "",
                    workspace_id,
                )
                exists = bool(
                    await conn.fetchval("SELECT to_regclass($1)", relation.sql)
                )
                if not exists:
                    raise HTTPException(404, f"dataset unavailable: {dataset}")
                columns = await published_relation_columns(conn, relation)
                if "workspace_id" not in columns:
                    raise HTTPException(
                        403, f"dataset is not workspace scoped: {dataset}"
                    )
                if "tenant_id" not in columns:
                    raise HTTPException(403, f"dataset is not tenant scoped: {dataset}")
                rows = await conn.fetch(
                    f"SELECT * FROM {relation.sql} WHERE workspace_id::text = $1 AND tenant_id::text = $2 LIMIT $3",
                    workspace_id,
                    tenant_id,
                    safe_limit,
                )
            return _gold_cache_set(cache_key, [dict(row) for row in rows])
    finally:
        await conn.close()


async def query_intelligence_dataset_rows(
    dataset: str, user: dict | None, limit: int = 5000
) -> list[dict[str, Any]]:
    """Read Intelligence only from the scoped, published Gold head."""
    return await query_gold_dataset_rows(dataset, user, limit)
