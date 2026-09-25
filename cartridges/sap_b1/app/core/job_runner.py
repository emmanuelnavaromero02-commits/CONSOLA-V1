from __future__ import annotations

import asyncio
import contextvars
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any

import asyncpg
import httpx
from app.core.request_context import scope_values
import requests as _requests

from app.core.config import settings
from app.core.request_context import get_security_context, refinement_security_context

REFINEMENT_URL = os.environ.get("REFINEMENT_URL", "http://refinement:8500")

_pool: asyncpg.Pool | None = None


async def _apply_scope(conn) -> tuple[str, str]:
    tenant_id, workspace_id = scope_values()
    await conn.execute(
        "SELECT set_config('app.tenant_id', $1, false), "
        "set_config('app.workspace_id', $2, false)",
        tenant_id or "",
        workspace_id or "",
    )
    return tenant_id, workspace_id
_tasks: dict[str, asyncio.Task] = {}


async def _get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        dsn = settings.database_url.replace("postgresql+psycopg2://", "postgresql://")
        _pool = await asyncpg.create_pool(dsn, min_size=1, max_size=3)
    return _pool


async def ensure_schema() -> None:
    pool = await _get_pool()
    await pool.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            job_id      TEXT PRIMARY KEY,
            tool        TEXT NOT NULL,
            args        JSONB DEFAULT '{}',
            status      TEXT DEFAULT 'running',
            message     TEXT,
            result      JSONB,
            error       TEXT,
            created_at  TIMESTAMPTZ DEFAULT NOW(),
            updated_at  TIMESTAMPTZ DEFAULT NOW(),
            finished_at TIMESTAMPTZ
        )
    """)
    await pool.execute(
        "CREATE INDEX IF NOT EXISTS idx_jobs_status "
        "ON jobs(status, created_at DESC)"
    )
    await pool.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS tenant_id UUID")
    await pool.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS workspace_id UUID")
    await pool.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS scope_status TEXT NOT NULL DEFAULT 'legacy_unscoped'")


async def cleanup_stale() -> None:
    try:
        pool = await _get_pool()
        await pool.execute(
            "UPDATE jobs SET status='failed', error='Process restarted', "
            "finished_at=NOW() WHERE status='running' AND tool LIKE 'sap_b1__%'"
        )
    except Exception:
        pass


async def _insert(job_id: str, tool: str, args: dict) -> None:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        tenant_id, workspace_id = await _apply_scope(conn)
        await conn.execute(
            "INSERT INTO jobs (job_id, tool, args, status, message, tenant_id, workspace_id, scope_status) "
            "VALUES ($1, $2, $3::jsonb, 'running', 'Queued', $4::uuid, $5::uuid, 'scoped')",
            job_id, tool, json.dumps(args), tenant_id or None, workspace_id or None,
        )


async def _update(
    job_id: str,
    status: str,
    message: str = "",
    result: Any = None,
    error: str = "",
) -> None:
    pool = await _get_pool()
    finished_at = datetime.now(timezone.utc) if status in ("done", "failed") else None
    async with pool.acquire() as conn:
        await _apply_scope(conn)
        await conn.execute(
            """UPDATE jobs
               SET status=$2, message=$3, result=$4::jsonb,
                   error=$5, updated_at=NOW(), finished_at=$6
               WHERE job_id=$1""",
            job_id,
            status,
            message,
            json.dumps(result) if result is not None else None,
            error[:4000] if error else "",
            finished_at,
        )


async def finish_external_job(job_id: str | None, result: Any) -> None:
    if job_id:
        await _update(job_id, "done", message="Completed by Airflow", result=result)


async def fail_external_job(job_id: str | None, error: str) -> None:
    if job_id:
        await _update(job_id, "failed", message="Failed in Airflow", error=error)


async def _log(
    job_id: str,
    entity: str | None,
    level: str,
    message: str,
    detail: dict | None = None,
) -> None:
    try:
        pool = await _get_pool()
        async with pool.acquire() as conn:
            tenant_id, workspace_id = await _apply_scope(conn)
            await conn.execute(
                "INSERT INTO run_logs (run_id, cartridge, entity, level, message, detail, tenant_id, workspace_id, scope_status) "
                "VALUES ($1, 'sap_b1', $2, $3, $4, $5::jsonb, $6::uuid, $7::uuid, 'scoped')",
                job_id, entity, level, message,
                json.dumps(detail) if detail else None,
                tenant_id or None, workspace_id or None,
            )
    except Exception:
        pass


async def create_extract_job(
    config: dict,
    from_date: str | None = None,
    to_date: str | None = None,
) -> dict:
    entity = config.get("entity", "unknown")
    mode   = config.get("mode", "full")
    job_id = str(uuid.uuid4())[:8]
    args   = {"entity": entity, "mode": mode, "from_date": from_date, "to_date": to_date}
    security_context = get_security_context()
    if security_context:
        config = {**config, "security_context": security_context}

    await _insert(job_id, "sap_b1__extract", args)

    if settings.airflow_url:
        await _trigger_airflow(job_id, config, from_date, to_date)
    else:
        task = asyncio.create_task(
            _run_extract(job_id, config, from_date, to_date),
            name=f"extract-{entity}-{job_id}",
        )
        _tasks[job_id] = task

    return {
        "job_id":  job_id,
        "status":  "running",
        "entity":  entity,
        "mode":    mode,
        "message": "Job started. Use get_job_status(job_id) to check progress.",
    }


async def create_extract_all_job(mode: str = "incremental") -> dict:
    job_id = str(uuid.uuid4())[:8]
    await _insert(job_id, "sap_b1__extract_all", {"mode": mode})
    security_context = get_security_context()

    task = asyncio.create_task(
        _run_extract_all(job_id, mode, security_context),
        name=f"extract-all-{job_id}",
    )
    _tasks[job_id] = task

    return {
        "job_id":  job_id,
        "status":  "running",
        "mode":    mode,
        "message": "Extracción batch iniciada. Use get_job_status(job_id) para ver avance.",
    }


async def get_job(job_id: str) -> dict:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        await _apply_scope(conn)
        row = await conn.fetchrow("SELECT * FROM jobs WHERE job_id=$1", job_id)
    if not row:
        return {"error": f"Job '{job_id}' not found"}
    return _row_to_dict(row)


async def list_jobs(limit: int = 10) -> list[dict]:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        await _apply_scope(conn)
        rows = await conn.fetch(
            "SELECT * FROM jobs WHERE tool LIKE 'sap_b1__%' "
            "ORDER BY created_at DESC LIMIT $1",
            min(limit, 50),
        )
    return [_row_to_dict(r) for r in rows]


async def _trigger_silver_refresh(entity: str, security_context: dict | None = None) -> None:
    source = f"raw/sap_b1/{entity}"
    api_key = os.environ.get("INTERNAL_API_KEY_CARTRIDGE_TO_REFINEMENT", "")
    if not api_key and os.environ.get("APP_ENV", "production").strip().lower() not in {"production", "prod"}:
        api_key = os.environ.get("INTERNAL_API_KEY", "")
    if not api_key:
        raise RuntimeError("Missing INTERNAL_API_KEY_CARTRIDGE_TO_REFINEMENT")
    async with httpx.AsyncClient(timeout=300) as client:
        response = await client.post(
            f"{REFINEMENT_URL}/refresh-by-source",
            headers={
                "x-api-key": api_key,
                "x-internal-service": "cartridge-sap_b1",
            },
            json={
                "source": source,
                "security_context": refinement_security_context(security_context),
            },
        )
    response.raise_for_status()
    payload = response.json()
    errors = [r for r in payload.get("results", []) if r.get("status") == "error"]
    if errors or payload.get("error"):
        raise RuntimeError(f"refresh-by-source failed for {source}: {payload}")


async def _trigger_airflow(
    job_id: str,
    config: dict,
    from_date: str | None,
    to_date: str | None,
) -> None:
    entity = config.get("entity", "")
    conf = {
        "job_id":            job_id,
        "entity":            entity,
        "mode":              config.get("mode", "full"),
        "from_date":         from_date or "",
        "to_date":           to_date or "",
        "watermark_field":   config.get("watermark_field") or "",
    }
    security_context = config.get("security_context")
    if isinstance(security_context, dict):
        conf["security_context"] = security_context
        if security_context.get("tenant_id"):
            conf["tenant_id"] = security_context["tenant_id"]
        if security_context.get("workspace_id"):
            conf["workspace_id"] = security_context["workspace_id"]
    url = f"{settings.airflow_url}/api/v1/dags/sap_b1_extract/dagRuns"
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None,
        lambda: _requests.post(
            url,
            auth=(settings.airflow_user, settings.airflow_password),
            json={"conf": conf},
            timeout=10,
        ).raise_for_status(),
    )


async def _run_extract_all(job_id: str, mode: str, security_context: dict | None = None) -> None:
    from app.services.catalog_service import get_all_entities
    from app.services.extraction_service import run_entity

    entities = get_all_entities()
    total = len(entities)
    completed = 0
    failed = 0
    results: list[dict] = []

    await _update(job_id, "running", f"Iniciando — {total} entidades en modo {mode}")
    await _log(job_id, None, "INFO", f"Batch iniciado: {total} entidades, modo={mode}")

    sem = asyncio.Semaphore(4)
    loop = asyncio.get_event_loop()

    async def _one(config: dict) -> None:
        nonlocal completed, failed
        entity = config.get("entity", "?")
        async with sem:
            await _log(job_id, entity, "INFO", "Iniciando extracción")
            try:
                overridden = dict(config)
                overridden["mode"] = mode
                if security_context:
                    overridden["security_context"] = security_context
                run_context = contextvars.copy_context()
                result = await loop.run_in_executor(
                    None, lambda c=overridden, ctx=run_context: ctx.run(run_entity, c)
                )
                count = result.get("record_count", 0)
                await _trigger_silver_refresh(entity, security_context)
                completed += 1
                await _log(
                    job_id, entity, "INFO",
                    f"Completado — {count:,} registros y refresh downstream",
                    {"record_count": count, "storage_uri": result.get("storage_uri")},
                )
                results.append({"entity": entity, "status": "success",
                                 "record_count": count})
            except Exception as exc:
                failed += 1
                await _log(job_id, entity, "ERROR", f"Error: {exc}",
                           {"error": str(exc)})
                results.append({"entity": entity, "status": "failed",
                                 "error": str(exc)})

            done = completed + failed
            await _update(
                job_id, "running",
                f"Progreso {done}/{total} — {completed} OK, {failed} errores",
            )

    await asyncio.gather(*[_one(dict(e)) for e in entities])

    total_records = sum(
        r.get("record_count", 0) for r in results if r["status"] == "success"
    )
    level = "INFO" if failed == 0 else "WARN"
    summary = (
        f"Completado — {completed}/{total} entidades, "
        f"{total_records:,} registros totales, {failed} errores"
    )
    final_status = "done" if failed == 0 else "failed"
    await _update(
        job_id, final_status,
        message=summary,
        result={"entities": results, "total_records": total_records,
                "completed": completed, "failed": failed},
        error="" if failed == 0 else f"{failed} entities failed",
    )
    await _log(job_id, None, level, summary)
    _tasks.pop(job_id, None)


async def _run_extract(
    job_id: str,
    config: dict,
    from_date: str | None,
    to_date: str | None,
) -> None:
    from app.services.extraction_service import run_entity

    entity = config.get("entity", "?")
    try:
        await _update(job_id, "running", f"Extracting {entity}…")
        loop = asyncio.get_event_loop()
        run_context = contextvars.copy_context()
        result = await loop.run_in_executor(
            None,
            lambda: run_context.run(run_entity, config, from_date=from_date, to_date=to_date),
        )
        await _trigger_silver_refresh(entity, config.get("security_context"))
        count = result.get("record_count", 0)
        await _update(
            job_id, "done",
            message=f"Completed — {count:,} records",
            result=result,
        )
    except Exception as exc:
        await _update(
            job_id, "failed",
            message=f"Failed: {exc}",
            error=str(exc),
        )
    finally:
        _tasks.pop(job_id, None)


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
