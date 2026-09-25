from __future__ import annotations

import json
import os

import asyncpg

from app.services.db_scope import scoped_db_for_user
from app.services.security_context import build_security_context

DATABASE_URL = os.environ.get("DATABASE_URL", "")

_pool: asyncpg.Pool | None = None
_JOB_COLUMNS = (
    "job_id, tool, args, status, message, result, error, "
    "created_at, updated_at, finished_at"
)
_PIPELINE_COLUMNS = (
    "run_id, dag_id, cartridge_id, entity, airflow_dag_run_id, mode, status, "
    "record_count, started_at, finished_at, error_message, extra"
)
_PIPELINE_COLUMN_EXISTS_CACHE: dict[str, bool] = {}


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
        pipeline_job = await _get_pipeline_run_as_job(pool, job_id, user=None)
        if pipeline_job:
            return pipeline_job
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
        pipeline_job = await _get_pipeline_run_as_job(pool, job_id, user=user)
        if pipeline_job:
            return pipeline_job
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
        jobs = [_row_to_dict(r) for r in rows]
        jobs.extend(await _list_recent_pipeline_jobs(pool, min(limit, 50), user=user))
        return _sort_jobs(jobs, min(limit, 50))
    rows = await pool.fetch(
        f"SELECT {_JOB_COLUMNS} FROM jobs ORDER BY created_at DESC LIMIT $1", min(limit, 50)
    )
    jobs = [_row_to_dict(r) for r in rows]
    jobs.extend(await _list_recent_pipeline_jobs(pool, min(limit, 50), user=None))
    return _sort_jobs(jobs, min(limit, 50))


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


def _sort_jobs(jobs: list[dict], limit: int) -> list[dict]:
    return sorted(
        jobs,
        key=lambda item: str(
            item.get("created_at")
            or item.get("updated_at")
            or item.get("finished_at")
            or ""
        ),
        reverse=True,
    )[:limit]


async def _pipeline_table_has_column(pool: asyncpg.Pool, column: str) -> bool:
    if column in _PIPELINE_COLUMN_EXISTS_CACHE:
        return _PIPELINE_COLUMN_EXISTS_CACHE[column]
    try:
        exists = await pool.fetchval(
            """
            SELECT EXISTS (
                SELECT 1
                  FROM information_schema.columns
                 WHERE table_schema='public'
                   AND table_name='pipeline_runs'
                   AND column_name=$1
            )
            """,
            column,
        )
        _PIPELINE_COLUMN_EXISTS_CACHE[column] = bool(exists)
        return bool(exists)
    except Exception:
        _PIPELINE_COLUMN_EXISTS_CACHE[column] = False
        return False


async def _pipeline_scope_sql(
    pool: asyncpg.Pool, user: dict | None, *, start_at: int = 1
) -> tuple[str, list]:
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

    has_tenant = await _pipeline_table_has_column(pool, "tenant_id")
    has_workspace = await _pipeline_table_has_column(pool, "workspace_id")
    if not (has_tenant and has_workspace):
        return "FALSE", []

    tenant_param = f"${start_at}"
    workspace_param = f"${start_at + 1}"
    allowed_param = f"${start_at + 2}"
    where = (
        f"tenant_id={tenant_param}::uuid "
        f"AND workspace_id={workspace_param}::uuid "
        f"AND ('*' = ANY({allowed_param}::text[]) "
        f"OR cartridge_id = ANY({allowed_param}::text[]))"
    )
    return where, [tenant_id, workspace_id, allowed]


async def _get_pipeline_run_as_job(
    pool: asyncpg.Pool, job_id: str, *, user: dict | None
) -> dict | None:
    try:
        where, params = await _pipeline_scope_sql(pool, user, start_at=2)
        if user is not None and build_security_context(user).get("workspace_id"):
            async with scoped_db_for_user(pool, user) as (conn, _tenant_id, _workspace_id):
                row = await conn.fetchrow(
                    f"""
                    SELECT {_PIPELINE_COLUMNS}
                      FROM pipeline_runs
                     WHERE (run_id=$1 OR airflow_dag_run_id=$1)
                       AND {where}
                     ORDER BY started_at DESC NULLS LAST
                     LIMIT 1
                    """,
                    job_id,
                    *params,
                )
        else:
            row = await pool.fetchrow(
                f"""
                SELECT {_PIPELINE_COLUMNS}
                  FROM pipeline_runs
                 WHERE (run_id=$1 OR airflow_dag_run_id=$1)
                   AND {where}
                 ORDER BY started_at DESC NULLS LAST
                 LIMIT 1
                """,
                job_id,
                *params,
            )
    except Exception:
        return None
    return _pipeline_row_to_job(row) if row else None


async def _list_recent_pipeline_jobs(
    pool: asyncpg.Pool, limit: int, *, user: dict | None
) -> list[dict]:
    try:
        where, params = await _pipeline_scope_sql(pool, user, start_at=2)
        if user is not None and build_security_context(user).get("workspace_id"):
            async with scoped_db_for_user(pool, user) as (conn, _tenant_id, _workspace_id):
                rows = await conn.fetch(
                    f"""
                    SELECT {_PIPELINE_COLUMNS}
                      FROM pipeline_runs
                     WHERE {where}
                     ORDER BY started_at DESC NULLS LAST
                     LIMIT $1
                    """,
                    limit,
                    *params,
                )
        else:
            rows = await pool.fetch(
                f"""
                SELECT {_PIPELINE_COLUMNS}
                  FROM pipeline_runs
                 WHERE {where}
                 ORDER BY started_at DESC NULLS LAST
                 LIMIT $1
                """,
                limit,
                *params,
            )
    except Exception:
        return []
    return [_pipeline_row_to_job(row) for row in rows]


def _pipeline_job_status(status: str) -> str:
    normalized = (status or "").strip().lower()
    if normalized in {"queued", "scheduled", "running", "up_for_retry"}:
        return "running"
    if normalized in {"failed", "error", "upstream_failed"}:
        return "failed"
    return "done"


def _pipeline_row_to_job(row) -> dict:
    d = dict(row)
    for k in ("started_at", "finished_at"):
        if d.get(k):
            d[k] = d[k].isoformat()
    extra = d.get("extra") or {}
    if isinstance(extra, str):
        try:
            extra = json.loads(extra)
        except Exception:
            extra = {}
    if not isinstance(extra, dict):
        extra = {}

    run_id = str(d.get("run_id") or d.get("airflow_dag_run_id") or "").strip()
    dag_run_id = str(d.get("airflow_dag_run_id") or d.get("run_id") or "").strip()
    pipeline_status = str(d.get("status") or "unknown").strip() or "unknown"
    job_status = _pipeline_job_status(pipeline_status)
    classification = extra.get("classification")
    if not isinstance(classification, dict):
        classification = {}
    reason = extra.get("reason") or extra.get("metadata_status") or classification.get("reason")
    message = f"Airflow {d.get('dag_id') or 'pipeline'} · {pipeline_status}"
    if reason:
        message = f"{message} · {reason}"
    if d.get("error_message"):
        message = f"{message} · {d.get('error_message')}"

    args = {
        "cartridge_id": d.get("cartridge_id"),
        "cartridge": d.get("cartridge_id"),
        "entity": d.get("entity"),
        "mode": d.get("mode"),
        "dag_id": d.get("dag_id"),
        "dag_run_id": dag_run_id,
    }
    record_count = d.get("record_count")
    if record_count is None:
        record_count = d.get("row_count")

    result = {
        "source": "pipeline_runs",
        "cartridge_id": d.get("cartridge_id"),
        "cartridge": d.get("cartridge_id"),
        "entity": d.get("entity"),
        "dag_id": d.get("dag_id"),
        "dag_run_id": dag_run_id,
        "airflow_dag_run_id": dag_run_id,
        "pipeline_status": pipeline_status,
        "record_count": record_count or 0,
        "total_records": record_count or 0,
        "extra": extra,
    }
    return {
        "job_id": run_id or dag_run_id,
        "tool": "airflow.pipeline_run",
        "args": args,
        "status": job_status,
        "message": message,
        "result": result,
        "error": d.get("error_message") if job_status == "failed" else None,
        "created_at": d.get("started_at"),
        "updated_at": d.get("finished_at") or d.get("started_at"),
        "finished_at": d.get("finished_at") if job_status != "running" else None,
    }


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
