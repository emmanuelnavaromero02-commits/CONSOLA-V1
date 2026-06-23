from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.services import auth


async def _table_exists(pool: Any) -> bool:
    return bool(await pool.fetchval("SELECT to_regclass('public.agent_schedule_runs')"))


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def reserve_scheduled_run(
    *,
    agent_id: str,
    tenant_id: str,
    workspace_id: str,
    scheduled_fire_at: datetime,
    schedule_key: str = "default",
    airflow_dag_run_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Reserve a scheduled agent fire-time exactly once.

    Fresh databases get the backing table through migration. Older local
    databases may not have it yet; in that case we stay permissive so scheduled
    agents continue to run while migrations catch up.
    """

    pool = await auth.pool()
    if not await _table_exists(pool):
        return {"reserved": True, "duplicate": False, "missing_table": True}

    fire_at = _to_utc(scheduled_fire_at)
    clean_key = str(schedule_key or "default").strip()[:120] or "default"
    meta_json = json.dumps(metadata or {}, default=str)
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true), "
                "set_config('app.workspace_id', $2, true)",
                tenant_id,
                workspace_id,
            )
            row = await conn.fetchrow(
                """
                INSERT INTO agent_schedule_runs (
                    tenant_id, workspace_id, agent_id, scheduled_fire_at, schedule_key,
                    airflow_dag_run_id, status, metadata
                )
                VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5, $6, 'running', $7::jsonb)
                ON CONFLICT (agent_id, schedule_key, scheduled_fire_at) DO NOTHING
                RETURNING id, status, agent_run_id, error_message
                """,
                tenant_id,
                workspace_id,
                agent_id,
                fire_at,
                clean_key,
                airflow_dag_run_id,
                meta_json,
            )
            if row:
                return {
                    "reserved": True,
                    "duplicate": False,
                    "id": row["id"],
                    "status": row["status"],
                    "agent_run_id": row["agent_run_id"],
                    "error_message": row["error_message"],
                }

            existing = await conn.fetchrow(
                """
                SELECT id, status, agent_run_id, error_message, started_at, finished_at
                  FROM agent_schedule_runs
                 WHERE tenant_id = $1::uuid
                   AND workspace_id = $2::uuid
                   AND agent_id = $3::uuid
                   AND schedule_key = $4
                   AND scheduled_fire_at = $5
                """,
                tenant_id,
                workspace_id,
                agent_id,
                clean_key,
                fire_at,
            )
    return {
        "reserved": False,
        "duplicate": True,
        "id": existing["id"] if existing else None,
        "status": existing["status"] if existing else "unknown",
        "agent_run_id": existing["agent_run_id"] if existing else None,
        "error_message": existing["error_message"] if existing else None,
        "started_at": existing["started_at"].isoformat() if existing and existing["started_at"] else None,
        "finished_at": existing["finished_at"].isoformat() if existing and existing["finished_at"] else None,
    }


async def finish_scheduled_run(
    *,
    schedule_run_id: int | None,
    agent_run_id: int | None,
    status: str,
    tenant_id: str | None = None,
    workspace_id: str | None = None,
    error_message: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    if schedule_run_id is None:
        return
    pool = await auth.pool()
    if not await _table_exists(pool):
        return
    clean_status = status if status in {"ok", "error", "cancelled", "skipped"} else "error"
    async with pool.acquire() as conn:
        async with conn.transaction():
            if tenant_id and workspace_id:
                await conn.execute(
                    "SELECT set_config('app.tenant_id', $1, true), "
                    "set_config('app.workspace_id', $2, true)",
                    tenant_id,
                    workspace_id,
                )
            await conn.execute(
                """
                UPDATE agent_schedule_runs
                   SET status = $2,
                       agent_run_id = COALESCE($3, agent_run_id),
                       finished_at = NOW(),
                       error_message = $4,
                       metadata = COALESCE(metadata, '{}'::jsonb) || $5::jsonb
                 WHERE id = $1
                """,
                schedule_run_id,
                clean_status,
                agent_run_id,
                error_message,
                json.dumps(metadata or {}, default=str),
            )
