from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.services import auth


class ScheduledRunAuthorityRetired(RuntimeError):
    authority_retired = True


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
    lease_seconds: int = 900,
) -> dict[str, Any]:
    """Reserve a scheduled agent fire-time exactly once.

    The durable reservation is a safety boundary. A database without it must
    not execute a scheduled monitor because a retry could duplicate alerts or
    decisions.
    """

    pool = await auth.pool()
    if not await _table_exists(pool):
        raise RuntimeError("scheduled-run idempotency storage unavailable")

    fire_at = _to_utc(scheduled_fire_at)
    lease_seconds = max(30, min(int(lease_seconds), 3600))
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
                    airflow_dag_run_id, status, metadata, heartbeat_at,
                    lease_expires_at, fencing_token
                )
                VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5, $6, 'running',
                        $7::jsonb, NOW(), NOW() + $8::int * INTERVAL '1 second', 1)
                ON CONFLICT (agent_id, schedule_key, scheduled_fire_at) DO NOTHING
                RETURNING id, status, agent_run_id, error_message, fencing_token
                """,
                tenant_id,
                workspace_id,
                agent_id,
                fire_at,
                clean_key,
                airflow_dag_run_id,
                meta_json,
                lease_seconds,
            )
            if row:
                return {
                    "reserved": True,
                    "duplicate": False,
                    "id": row["id"],
                    "status": row["status"],
                    "agent_run_id": row["agent_run_id"],
                    "error_message": row["error_message"],
                    "fencing_token": int(row["fencing_token"]),
                }

            existing = await conn.fetchrow(
                """
                SELECT id, status, agent_run_id, error_message, started_at, finished_at,
                       lease_expires_at, fencing_token
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
            if existing is None:
                raise RuntimeError("scheduled-run reservation is not visible")
            if existing["status"] == "running" and (
                existing["lease_expires_at"] is None
                or _to_utc(existing["lease_expires_at"]) <= datetime.now(timezone.utc)
            ):
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended($1,0))",
                    f"agent_schedule_effect:{existing['id']}",
                )
                reclaimed = await conn.fetchrow(
                    """UPDATE agent_schedule_runs
                          SET started_at=NOW(), finished_at=NULL, error_message=NULL,
                              heartbeat_at=NOW(),
                              lease_expires_at=NOW() + $2::int * INTERVAL '1 second',
                              fencing_token=fencing_token + 1
                        WHERE id=$1 AND status='running'
                          AND (lease_expires_at IS NULL OR lease_expires_at <= NOW())
                        RETURNING id,status,agent_run_id,error_message,fencing_token""",
                    existing["id"],
                    lease_seconds,
                )
                if reclaimed:
                    return {
                        "reserved": True,
                        "duplicate": False,
                        "id": reclaimed["id"],
                        "status": reclaimed["status"],
                        "agent_run_id": reclaimed["agent_run_id"],
                        "error_message": reclaimed["error_message"],
                        "fencing_token": int(reclaimed["fencing_token"]),
                    }
    return {
        "reserved": False,
        "duplicate": True,
        "id": existing["id"] if existing else None,
        "status": existing["status"] if existing else "unknown",
        "agent_run_id": existing["agent_run_id"] if existing else None,
        "error_message": existing["error_message"] if existing else None,
        "started_at": existing["started_at"].isoformat()
        if existing and existing["started_at"]
        else None,
        "finished_at": existing["finished_at"].isoformat()
        if existing and existing["finished_at"]
        else None,
        "fencing_token": int(existing["fencing_token"]) if existing else None,
    }


async def finish_scheduled_run(
    *,
    schedule_run_id: int | None,
    agent_run_id: int | None,
    status: str,
    tenant_id: str | None = None,
    workspace_id: str | None = None,
    fencing_token: int,
    error_message: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> bool:
    if schedule_run_id is None:
        return False
    pool = await auth.pool()
    if not await _table_exists(pool):
        raise RuntimeError("scheduled-run idempotency storage unavailable")
    clean_status = (
        status if status in {"ok", "error", "cancelled", "skipped"} else "error"
    )
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
                "SELECT pg_advisory_xact_lock(hashtextextended($1,0))",
                f"agent_schedule_effect:{schedule_run_id}",
            )
            outcome = await conn.execute(
                """
                UPDATE agent_schedule_runs
                   SET status = $2,
                       agent_run_id = COALESCE($3, agent_run_id),
                       finished_at = NOW(),
                       lease_expires_at = NULL,
                       error_message = $4,
                       metadata = COALESCE(metadata, '{}'::jsonb) || $5::jsonb
                 WHERE id = $1 AND status='running' AND fencing_token=$6
                   AND lease_expires_at > clock_timestamp()
                """,
                schedule_run_id,
                clean_status,
                agent_run_id,
                error_message,
                json.dumps(metadata or {}, default=str),
                fencing_token,
            )
            if outcome != "UPDATE 1":
                state = await conn.fetchrow(
                    """SELECT status,fencing_token,
                              lease_expires_at > clock_timestamp() AS lease_live
                         FROM agent_schedule_runs WHERE id=$1""",
                    schedule_run_id,
                )
                if state is None:
                    raise RuntimeError("scheduled-run reservation is unavailable")
                if (
                    state["status"] != "running"
                    or int(state["fencing_token"]) != fencing_token
                    or state["lease_live"] is not True
                ):
                    raise ScheduledRunAuthorityRetired(
                        "scheduled-run reservation is not available"
                    )
                raise RuntimeError("scheduled-run reservation is not available")
            return True


async def heartbeat_scheduled_run(
    *,
    schedule_run_id: int,
    tenant_id: str,
    workspace_id: str,
    fencing_token: int,
    lease_seconds: int = 900,
) -> None:
    pool = await auth.pool()
    lease_seconds = max(30, min(int(lease_seconds), 3600))
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.tenant_id',$1,true), "
                "set_config('app.workspace_id',$2,true)",
                tenant_id,
                workspace_id,
            )
            outcome = await conn.execute(
                """UPDATE agent_schedule_runs
                      SET heartbeat_at=NOW(),
                          lease_expires_at=NOW() + $3::int * INTERVAL '1 second'
                    WHERE id=$1 AND fencing_token=$2 AND status='running'
                      AND lease_expires_at > NOW()""",
                schedule_run_id,
                fencing_token,
                lease_seconds,
            )
            if outcome != "UPDATE 1":
                raise RuntimeError("scheduled-run lease is unavailable")
