from __future__ import annotations

import os
import re
from typing import Any

import asyncpg
from fastapi import HTTPException

from app.services.intelligence.utils import workspace_scope


_SAFE_DATASET_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")


def _normalize_dsn(raw: str) -> str:
    return (raw or "").replace("postgresql+psycopg2://", "postgresql://")


def _gold_dsn() -> str:
    return _normalize_dsn(os.environ.get("GOLD_DATABASE_URL") or os.environ.get("DATABASE_URL") or "")


def _gold_table(dataset: str) -> str:
    clean = str(dataset or "").strip()
    if not _SAFE_DATASET_RE.fullmatch(clean):
        raise HTTPException(400, "invalid intelligence dataset")
    return f"gold_{clean}"


async def _table_columns(conn: asyncpg.Connection, table: str) -> set[str]:
    rows = await conn.fetch(
        """
        SELECT column_name
          FROM information_schema.columns
         WHERE table_schema = 'public'
           AND table_name = $1
        """,
        table,
    )
    return {str(row["column_name"]) for row in rows}


async def query_gold_dataset_rows(dataset: str, user: dict | None, limit: int = 5000) -> list[dict[str, Any]]:
    """Read workspace-scoped Gold rows directly for intelligence runs.

    Refinement can still serve datasets for legacy flows, but the intelligence
    readiness gate verifies Gold tables directly. This fetcher keeps the run path
    aligned with that gate and refuses unscoped Gold reads for authenticated users.
    """
    dsn = _gold_dsn()
    if not dsn:
        raise HTTPException(503, "gold database unavailable")
    table = _gold_table(dataset)
    tenant_id, workspace_id = workspace_scope(user)
    safe_limit = max(1, min(int(limit or 5000), 5000))
    conn = await asyncpg.connect(dsn, command_timeout=10)
    try:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true), set_config('app.workspace_id', $2, true)",
                tenant_id or "",
                workspace_id,
            )
            exists = bool(await conn.fetchval("SELECT to_regclass($1)", f"public.{table}"))
            if not exists:
                raise HTTPException(404, f"dataset unavailable: {dataset}")
            columns = await _table_columns(conn, table)
            if "workspace_id" not in columns:
                raise HTTPException(403, f"dataset is not workspace scoped: {dataset}")
            if "tenant_id" in columns and tenant_id:
                rows = await conn.fetch(
                    f'SELECT * FROM public."{table}" WHERE workspace_id::text = $1 AND tenant_id::text = $2 LIMIT $3',
                    workspace_id,
                    tenant_id,
                    safe_limit,
                )
            else:
                rows = await conn.fetch(
                    f'SELECT * FROM public."{table}" WHERE workspace_id::text = $1 LIMIT $2',
                    workspace_id,
                    safe_limit,
                )
        return [dict(row) for row in rows]
    finally:
        await conn.close()


async def query_intelligence_dataset_rows(dataset: str, user: dict | None, limit: int = 5000) -> list[dict[str, Any]]:
    """Prefer scoped Gold data, then fall back to the existing Refinement path.

    A present but empty/scoped Gold table returns an empty list instead of falling
    back, because fallback could mask a tenant/workspace data gap with unrelated
    rows from another source.
    """
    try:
        return await query_gold_dataset_rows(dataset, user, limit)
    except HTTPException as exc:
        if exc.status_code not in {404, 503}:
            raise
    except Exception:
        pass

    from app.services import control_room_service

    return await control_room_service.query_dataset_rows(dataset, user, limit)
