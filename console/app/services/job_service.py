"""
Job service — read-only view of the jobs table.

Job creation and execution is the cartridge's responsibility (e.g. replicon's job_runner).
This module provides read access for the sidebar and REST endpoints.
"""
from __future__ import annotations

import json
import os

import asyncpg

from app.services.security_context import build_security_context

DATABASE_URL = os.environ.get("DATABASE_URL", "")

_pool: asyncpg.Pool | None = None
_JOB_COLUMNS = (
    "job_id, tool, args, status, message, result, error, "
    "created_at, updated_at, finished_at"
)


async def _get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        dsn = DATABASE_URL.replace("postgresql+psycopg2://", "postgresql://")
        if not dsn:
            raise RuntimeError("DATABASE_URL is not configured (job_service)")
        _pool = await asyncpg.create_pool(dsn, min_size=1, max_size=3, command_timeout=10)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def get(job_id: str) -> dict:
    pool = await _get_pool()
    row = await pool.fetchrow(f"SELECT {_JOB_COLUMNS} FROM jobs WHERE job_id=$1", job_id)
    if not row:
        return {"error": f"Job '{job_id}' not found"}
    return _row_to_dict(row)


async def get_scoped(job_id: str, user: dict | None = None) -> dict:
    if user is None:
        return await get(job_id)
    pool = await _get_pool()
    where, params = _job_scope_sql(user, start_at=2)
    row = await pool.fetchrow(
        f"""
        SELECT {_JOB_COLUMNS}
          FROM jobs
         WHERE job_id = $1
           AND {where}
        """,
        job_id,
        *params,
    )
    if not row:
        return {"error": f"Job '{job_id}' not found"}
    return _row_to_dict(row)


async def list_recent(limit: int = 10, user: dict | None = None) -> list[dict]:
    pool = await _get_pool()
    if user is not None:
        where, params = _job_scope_sql(user, start_at=1)
        rows = await pool.fetch(
            f"""
            SELECT {_JOB_COLUMNS}
              FROM jobs
             WHERE {where}
             ORDER BY created_at DESC
             LIMIT ${len(params) + 1}
            """,
            *params,
            min(limit, 50),
        )
        return [_row_to_dict(r) for r in rows]
    rows = await pool.fetch(
        f"SELECT {_JOB_COLUMNS} FROM jobs ORDER BY created_at DESC LIMIT $1", min(limit, 50)
    )
    return [_row_to_dict(r) for r in rows]


def _row_to_dict(row) -> dict:
    d = dict(row)
    for k in ("created_at", "updated_at", "finished_at"):
        if d.get(k):
            d[k] = d[k].isoformat()
    for k in ("args", "result"):
        if isinstance(d.get(k), str):
            try:
                d[k] = json.loads(d[k])
            except Exception:
                pass
    return d


def _job_allowed(job: dict, user: dict | None) -> bool:
    if user is None:
        return True
    ctx = build_security_context(user)
    allowed = {
        str(item).strip()
        for item in (ctx.get("allowed_cartridges") or [])
        if str(item).strip()
    }
    if "*" in allowed and not (ctx.get("tenant_id") or ctx.get("workspace_id")):
        return True

    args = job.get("args") if isinstance(job.get("args"), dict) else {}
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    tenant_id = str(args.get("tenant_id") or result.get("tenant_id") or "").strip()
    workspace_id = str(args.get("workspace_id") or result.get("workspace_id") or "").strip()
    cartridge = str(
        args.get("cartridge_id")
        or args.get("cartridge")
        or result.get("cartridge_id")
        or result.get("cartridge")
        or ""
    ).strip()
    ctx_tenant = str(ctx.get("tenant_id") or "").strip()
    ctx_workspace = str(ctx.get("workspace_id") or "").strip()
    if ctx_tenant or ctx_workspace:
        if not tenant_id or not workspace_id:
            return False
        if ctx_tenant and tenant_id != ctx_tenant:
            return False
        if ctx_workspace and workspace_id != ctx_workspace:
            return False
    if cartridge:
        return "*" in allowed or cartridge in allowed
    return False


def _job_scope_sql(user: dict | None, *, start_at: int = 1) -> tuple[str, list]:
    if user is None:
        return "TRUE", []
    ctx = build_security_context(user)
    tenant_id = str(ctx.get("tenant_id") or "").strip()
    workspace_id = str(ctx.get("workspace_id") or "").strip()
    allowed = [
        str(item).strip()
        for item in (ctx.get("allowed_cartridges") or [])
        if str(item).strip()
    ]
    if "*" in allowed and not (tenant_id or workspace_id):
        return "TRUE", []
    if not tenant_id or not workspace_id:
        return "FALSE", []

    tenant_param = f"${start_at}"
    workspace_param = f"${start_at + 1}"
    allowed_param = f"${start_at + 2}"
    scope_expr = (
        "COALESCE(args->>'tenant_id', result->>'tenant_id', '') = "
        f"{tenant_param} "
        "AND COALESCE(args->>'workspace_id', result->>'workspace_id', '') = "
        f"{workspace_param}"
    )
    cartridge_expr = (
        "COALESCE(args->>'cartridge_id', args->>'cartridge', "
        "result->>'cartridge_id', result->>'cartridge', '')"
    )
    where = (
        f"{scope_expr} AND ("
        f"'*' = ANY({allowed_param}::text[]) "
        f"OR ({cartridge_expr} <> '' AND {cartridge_expr} = ANY({allowed_param}::text[]))"
        ")"
    )
    return where, [tenant_id, workspace_id, allowed]
