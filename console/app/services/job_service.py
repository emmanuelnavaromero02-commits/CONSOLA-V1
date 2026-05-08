"""
Job service — read-only view of the jobs table.

Job creation and execution is the cartridge's responsibility (e.g. replicon's job_runner).
This module provides read access for the sidebar and REST endpoints.
"""
from __future__ import annotations

import json
import os

import asyncpg

DATABASE_URL = os.environ.get("DATABASE_URL", "")

_pool: asyncpg.Pool | None = None


async def _get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        dsn = DATABASE_URL.replace("postgresql+psycopg2://", "postgresql://")
        _pool = await asyncpg.create_pool(dsn, min_size=1, max_size=3)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def get(job_id: str) -> dict:
    pool = await _get_pool()
    row = await pool.fetchrow("SELECT * FROM jobs WHERE job_id=$1", job_id)
    if not row:
        return {"error": f"Job '{job_id}' not found"}
    return _row_to_dict(row)


async def list_recent(limit: int = 10) -> list[dict]:
    pool = await _get_pool()
    rows = await pool.fetch(
        "SELECT * FROM jobs ORDER BY created_at DESC LIMIT $1", min(limit, 50)
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
