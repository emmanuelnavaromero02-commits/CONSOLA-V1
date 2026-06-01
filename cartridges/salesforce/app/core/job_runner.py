"""
Salesforce Batch Job Runner
=========================
Manages async extraction jobs within the cartridge process.

- Jobs are persisted to the shared service DB (jobs table).
- Each job runs as an asyncio Task; sync extraction code runs in a thread pool.
- The MCP tools expose create / status / list to the LLM.
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any

import asyncpg
import httpx
import requests as _requests

from app.core.config import settings
from app.core.request_context import get_security_context, refinement_security_context

REFINEMENT_URL = os.environ.get("REFINEMENT_URL", "http://refinement:8500")

_pool: asyncpg.Pool | None = None
_tasks: dict[str, asyncio.Task] = {}


# ── DB pool ───────────────────────────────────────────────────────────────────

async def _get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        # asyncpg needs postgresql:// not postgresql+psycopg2://
        dsn = settings.database_url.replace("postgresql+psycopg2://", "postgresql://")
        _pool = await asyncpg.create_pool(dsn, min_size=1, max_size=3)
    return _pool


# ── Schema migration ──────────────────────────────────────────────────────────

async def ensure_schema() -> None:
    """Create the jobs table if it doesn't exist (idempotent)."""
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


async def cleanup_stale() -> None:
    """Mark jobs stuck in 'running' at process startup as failed."""
    try:
        pool = await _get_pool()
        await pool.execute(
            "UPDATE jobs SET status='failed', error='Process restarted', "
            "finished_at=NOW() WHERE status='running' AND tool LIKE 'salesforce__%'"
        )
    except Exception:
        pass


# ── CRUD helpers ──────────────────────────────────────────────────────────────

async def _insert(job_id: str, tool: str, args: dict) -> None:
    pool = await _get_pool()
    await pool.execute(
        "INSERT INTO jobs (job_id, tool, args, status, message) "
        "VALUES ($1, $2, $3::jsonb, 'running', 'Queued')",
        job_id, tool, json.dumps(args),
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
    await pool.execute(
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


# ── Central log writer ───────────────────────────────────────────────────────

async def _log(
    job_id: str,
    entity: str | None,
    level: str,
    message: str,
    detail: dict | None = None,
) -> None:
    """Write a progress entry to the central run_logs table."""
    try:
        pool = await _get_pool()
        await pool.execute(
            "INSERT INTO run_logs (run_id, cartridge, entity, level, message, detail) "
            "VALUES ($1, 'salesforce', $2, $3, $4, $5::jsonb)",
            job_id, entity, level, message,
            json.dumps(detail) if detail else None,
        )
    except Exception:
        pass  # logs are best-effort


# ── Public API ────────────────────────────────────────────────────────────────

async def create_extract_job(
    config: dict,
    from_date: str | None = None,
    to_date: str | None = None,
) -> dict:
    """
    Create a background extraction job and return immediately.
    - Si AIRFLOW_URL está configurado: delega al DAG salesforce_extract en Airflow.
    - Si no: corre la extracción inline en un asyncio Task (comportamiento original).
    The LLM should use get_job_status(job_id) to track progress.
    """
    entity = config.get("entity", "unknown")
    mode   = config.get("mode", "full")
    job_id = str(uuid.uuid4())[:8]
    args   = {"entity": entity, "mode": mode, "from_date": from_date, "to_date": to_date}
    security_context = config.get("security_context")
    if not isinstance(security_context, dict):
        security_context = get_security_context()
    if security_context:
        config = {**config, "security_context": security_context}
        args["security_context"] = security_context

    await _insert(job_id, "salesforce__extract", args)

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


async def create_extract_all_job(
    mode: str = "incremental",
    security_context: dict | None = None,
) -> dict:
    """
    Extract all enabled entities in parallel (max 4 concurrent).
    Logs progress to run_logs; updates job message after each entity.
    """
    job_id = str(uuid.uuid4())[:8]
    security_context = security_context if security_context is not None else get_security_context()
    args = {"mode": mode}
    if security_context:
        args["security_context"] = security_context
    await _insert(job_id, "salesforce__extract_all", args)

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
    row = await pool.fetchrow("SELECT * FROM jobs WHERE job_id=$1", job_id)
    if not row:
        return {"error": f"Job '{job_id}' not found"}
    return _row_to_dict(row)


async def list_jobs(limit: int = 10) -> list[dict]:
    pool = await _get_pool()
    rows = await pool.fetch(
        "SELECT * FROM jobs WHERE tool LIKE 'salesforce__%' "
        "ORDER BY created_at DESC LIMIT $1",
        min(limit, 50),
    )
    return [_row_to_dict(r) for r in rows]


# ── Silver refresh trigger ────────────────────────────────────────────────────

async def _trigger_silver_refresh(
    entity: str,
    security_context: dict | None = None,
) -> dict:
    """
    Notifica al refinement engine que hay nuevos datos Bronze para esta entidad.
    El engine re-materializa todos los datasets Silver que dependen de esa fuente.
    Fail-fast: si refresh-by-source falla, el job debe quedar fallido.
    """
    source = f"raw/salesforce/{entity}"
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
                "x-internal-service": "cartridge-salesforce",
            },
            json={
                "source": source,
                "security_context": refinement_security_context(security_context),
            },
        )
    response.raise_for_status()
    try:
        payload = response.json()
    except ValueError:
        payload = {"raw": response.text}
    errors = [
        r
        for r in payload.get("results", [])
        if isinstance(r, dict) and r.get("status") == "error"
    ] if isinstance(payload, dict) else []
    if errors or (isinstance(payload, dict) and payload.get("error")):
        raise RuntimeError(f"refresh-by-source failed for {source}: {payload}")
    return {"source": source, "status_code": response.status_code, "result": payload}


# ── Airflow trigger ───────────────────────────────────────────────────────────

async def _trigger_airflow(
    job_id: str,
    config: dict,
    from_date: str | None,
    to_date: str | None,
) -> None:
    """POST to Airflow REST API to trigger the salesforce_extract DAG."""
    entity = config.get("entity", "")
    conf = {
        "job_id":            job_id,
        "entity":            entity,
        "mode":              config.get("mode", "full"),
        "from_date":         from_date or "",
        "to_date":           to_date or "",
        "watermark_field":   config.get("watermark_field") or "",
        "sf_base_url": settings.sf_base_url,
    }
    security_context = config.get("security_context")
    if isinstance(security_context, dict):
        conf["security_context"] = security_context
        if security_context.get("tenant_id"):
            conf["tenant_id"] = security_context["tenant_id"]
        if security_context.get("workspace_id"):
            conf["workspace_id"] = security_context["workspace_id"]
    url = f"{settings.airflow_url}/api/v1/dags/salesforce_extract/dagRuns"
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


# ── Background executor ───────────────────────────────────────────────────────

async def _run_extract_all(
    job_id: str,
    mode: str,
    security_context: dict | None = None,
) -> None:
    from app.services.catalog_service import get_all_entities
    from app.services.extraction_service import run_entity

    entities = get_all_entities()
    total = len(entities)
    completed = 0
    failed = 0
    results: list[dict] = []

    await _update(job_id, "running", f"Iniciando — {total} entidades en modo {mode}")
    await _log(job_id, None, "INFO", f"Batch iniciado: {total} entidades, modo={mode}")

    sem = asyncio.Semaphore(4)          # máx 4 extracciones en paralelo
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
                result = await loop.run_in_executor(
                    None, lambda c=overridden: run_entity(c)
                )
                count = result.get("record_count", 0)
                refresh = await _trigger_silver_refresh(entity, security_context)
                completed += 1
                await _log(
                    job_id, entity, "INFO",
                    f"Completado — {count:,} registros y refresh downstream",
                    {
                        "record_count": count,
                        "storage_uri": result.get("storage_uri"),
                        "silver_refresh": refresh,
                    },
                )
                results.append({"entity": entity, "status": "success",
                                 "record_count": count, "silver_refresh": refresh})
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
        result = await loop.run_in_executor(
            None,
            lambda: run_entity(config, from_date=from_date, to_date=to_date),
        )
        refresh = await _trigger_silver_refresh(entity, config.get("security_context"))
        result = {**result, "silver_refresh": refresh}
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
