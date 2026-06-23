"""
SAP SuccessFactors Batch Job Runner
=========================
Manages async extraction jobs within the cartridge process.

- Jobs are persisted to the shared service DB (jobs table).
- Each job runs as an asyncio Task; sync extraction code runs in a thread pool.
- The MCP tools expose create / status / list to the LLM.
"""
from __future__ import annotations

import asyncio
import contextvars
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import asyncpg
import httpx
from app.core.request_context import scope_values
import requests as _requests

from app.core.config import settings
from app.core.extraction_status import (
    classify_extraction_exception,
    classify_successful_extraction,
    summarize_extraction_results,
)
from app.core.request_context import get_security_context, refinement_security_context

REFINEMENT_URL = os.environ.get("REFINEMENT_URL", "http://refinement:8500")

SUCCESSFACTORS_GOLD_FOUNDATION_ORDER = [
    "sap_successfactors_employee_360",
    "sap_successfactors_org_structure",
    "sap_successfactors_headcount_by_location",
    "sap_successfactors_headcount_by_department",
    "sap_successfactors_headcount_by_company",
    "sap_successfactors_manager_hierarchy",
]

SUCCESSFACTORS_GOLD_TALENT_ORDER = [
    "sap_successfactors_talent_employee_profile",
    "sap_successfactors_talent_role_profile",
    "sap_successfactors_talent_mobility_history",
    "sap_successfactors_talent_cpa_scores",
    "sap_successfactors_talent_readiness",
    "sap_successfactors_talent_9box",
    "sap_successfactors_talent_9box_operational",
    "sap_successfactors_talent_retention_risk",
    "sap_successfactors_talent_promotion_alignment",
    "sap_successfactors_talent_calibration_sensitivity",
    "sap_successfactors_talent_role_fit_assignments",
    "sap_successfactors_talent_action_candidates",
    "sap_successfactors_talent_signals",
]

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


def _refinement_auth() -> tuple[str, str]:
    airflow_key = os.environ.get("INTERNAL_API_KEY_AIRFLOW_TO_REFINEMENT", "")
    if airflow_key:
        return airflow_key, "airflow"
    cartridge_key = os.environ.get("INTERNAL_API_KEY_CARTRIDGE_TO_REFINEMENT", "")
    if cartridge_key:
        return cartridge_key, "cartridge-sap_successfactors"
    if os.environ.get("APP_ENV", "production").strip().lower() not in {"production", "prod"}:
        legacy = os.environ.get("INTERNAL_API_KEY", "")
        if legacy:
            return legacy, "cartridge-sap_successfactors"
    raise RuntimeError("Missing INTERNAL_API_KEY_AIRFLOW_TO_REFINEMENT or INTERNAL_API_KEY_CARTRIDGE_TO_REFINEMENT")


def _refinement_security_context_for_service(
    security_context: dict | None,
    internal_service: str,
) -> dict[str, Any]:
    source = "airflow" if internal_service == "airflow" else "cartridge-sap_successfactors"
    return refinement_security_context(security_context, source=source)


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
    await pool.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS tenant_id UUID")
    await pool.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS workspace_id UUID")
    await pool.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS scope_status TEXT NOT NULL DEFAULT 'legacy_unscoped'")


async def cleanup_stale() -> None:
    """Mark jobs stuck in 'running' at process startup as failed."""
    try:
        pool = await _get_pool()
        await pool.execute(
            "UPDATE jobs SET status='failed', error='Process restarted', "
            "finished_at=NOW() WHERE status='running' AND tool LIKE 'sap_successfactors__%'"
        )
    except Exception:
        pass


# ── CRUD helpers ──────────────────────────────────────────────────────────────

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
        async with pool.acquire() as conn:
            tenant_id, workspace_id = await _apply_scope(conn)
            await conn.execute(
                "INSERT INTO run_logs (run_id, cartridge, entity, level, message, detail, tenant_id, workspace_id, scope_status) "
                "VALUES ($1, 'sap_successfactors', $2, $3, $4, $5::jsonb, $6::uuid, $7::uuid, 'scoped')",
                job_id, entity, level, message,
                json.dumps(detail) if detail else None,
                tenant_id or None, workspace_id or None,
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
    - Si AIRFLOW_URL está configurado: delega al DAG sap_successfactors_extract en Airflow.
    - Si no: corre la extracción inline en un asyncio Task (comportamiento original).
    The LLM should use get_job_status(job_id) to track progress.
    """
    entity = config.get("entity", "unknown")
    mode   = config.get("mode", "full")
    job_id = str(uuid.uuid4())[:8]
    args   = {"entity": entity, "mode": mode, "from_date": from_date, "to_date": to_date}
    conn_id = (str(config.get("conn_id") or config.get("connection_id") or "").strip() or None)
    if conn_id:
        args["conn_id"] = conn_id
        config = {**config, "conn_id": conn_id}
    security_context = get_security_context()
    if security_context:
        config = {**config, "security_context": security_context}

    await _insert(job_id, "sap_successfactors__extract", args)

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
    conn_id: str | None = None,
    target: str = "all",
) -> dict:
    """
    Extract all enabled entities in parallel (max 4 concurrent).
    Logs progress to run_logs; updates job message after each entity.
    """
    job_id = str(uuid.uuid4())[:8]
    selected_conn_id = (conn_id or "").strip() or None
    normalized_target = str(target or "all").strip().lower()
    if normalized_target not in {"all", "foundation", "talent"}:
        normalized_target = "all"
    args = {"mode": mode, "target": normalized_target}
    if selected_conn_id:
        args["conn_id"] = selected_conn_id
    await _insert(job_id, "sap_successfactors__extract_all", args)
    security_context = get_security_context()

    task = asyncio.create_task(
        _run_extract_all(job_id, mode, security_context, selected_conn_id, normalized_target),
        name=f"extract-all-{job_id}",
    )
    _tasks[job_id] = task

    return {
        "job_id":  job_id,
        "status":  "running",
        "mode":    mode,
        "target":  normalized_target,
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
            "SELECT * FROM jobs WHERE tool LIKE 'sap_successfactors__%' "
            "ORDER BY created_at DESC LIMIT $1",
            min(limit, 50),
        )
    return [_row_to_dict(r) for r in rows]


# ── Silver refresh trigger ────────────────────────────────────────────────────

async def _trigger_silver_refresh(entity: str, security_context: dict | None = None) -> None:
    """
    Notifica al refinement engine que hay nuevos datos Bronze para esta entidad.
    El engine re-materializa todos los datasets Silver que dependen de esa fuente.
    Fail-fast: si refresh-by-source falla, el job debe quedar fallido.
    """
    source = f"raw/sap_successfactors/{entity}"
    api_key, internal_service = _refinement_auth()
    async with httpx.AsyncClient(timeout=300) as client:
        response = await client.post(
            f"{REFINEMENT_URL}/refresh-by-source",
            headers={
                "x-api-key": api_key,
                "x-internal-service": internal_service,
            },
            json={
                "source": source,
                "security_context": _refinement_security_context_for_service(
                    security_context,
                    internal_service,
                ),
            },
        )
    response.raise_for_status()
    payload = response.json()
    errors = [r for r in payload.get("results", []) if r.get("status") == "error"]
    if errors or payload.get("error"):
        raise RuntimeError(f"refresh-by-source failed for {source}: {payload}")


def _successfactors_gold_datasets_for_target(target: str = "all") -> list[str]:
    target = str(target or "all").strip().lower()
    if target == "foundation":
        return list(SUCCESSFACTORS_GOLD_FOUNDATION_ORDER)
    if target == "talent":
        return list(SUCCESSFACTORS_GOLD_FOUNDATION_ORDER) + list(SUCCESSFACTORS_GOLD_TALENT_ORDER)
    return list(SUCCESSFACTORS_GOLD_FOUNDATION_ORDER) + list(SUCCESSFACTORS_GOLD_TALENT_ORDER)


async def _trigger_successfactors_gold_refresh(
    target: str = "all",
    security_context: dict | None = None,
) -> dict[str, Any]:
    """Materialize SuccessFactors Gold once after aggregate extraction.

    Entity-level refresh-by-source keeps Silver current. The master extract-all
    path then refreshes the known SuccessFactors Gold order once so Control Room
    reads fresh Gold/Talent without each entity re-running the full Gold graph.
    """
    datasets = _successfactors_gold_datasets_for_target(target)
    api_key, internal_service = _refinement_auth()
    headers = {
        "x-api-key": api_key,
        "x-internal-service": internal_service,
        "x-security-context": json.dumps(
            _refinement_security_context_for_service(
                security_context,
                internal_service,
            ),
            ensure_ascii=False,
        ),
    }
    results: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=600) as client:
        for name in datasets:
            try:
                response = await client.post(
                    f"{REFINEMENT_URL}/datasets/{quote(name, safe='')}/refresh",
                    headers=headers,
                )
                try:
                    payload = response.json()
                except Exception:
                    payload = {"text": response.text[:300]}
                if response.status_code >= 400:
                    results.append(
                        {
                            "name": name,
                            "status": "error",
                            "status_code": response.status_code,
                            "error": payload,
                        }
                    )
                    continue
                results.append(
                    {
                        "name": name,
                        "status": "ok",
                        "row_count": payload.get("row_count"),
                        "storage_uri": payload.get("storage_uri"),
                    }
                )
            except Exception as exc:  # noqa: BLE001
                results.append(
                    {
                        "name": name,
                        "status": "error",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
    ok = sum(1 for item in results if item.get("status") == "ok")
    return {
        "status": "success" if ok == len(results) else "partial" if ok else "failed",
        "target": target,
        "materialized": ok,
        "total": len(results),
        "results": results,
    }


# ── Airflow trigger ───────────────────────────────────────────────────────────

async def _trigger_airflow(
    job_id: str,
    config: dict,
    from_date: str | None,
    to_date: str | None,
) -> None:
    """POST to Airflow REST API to trigger the sap_successfactors_extract DAG."""
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
    conn_id = (str(config.get("conn_id") or config.get("connection_id") or "").strip() or None)
    if conn_id:
        conf["conn_id"] = conn_id
    security_context = config.get("security_context")
    if isinstance(security_context, dict):
        conf["security_context"] = security_context
        if security_context.get("tenant_id"):
            conf["tenant_id"] = security_context["tenant_id"]
        if security_context.get("workspace_id"):
            conf["workspace_id"] = security_context["workspace_id"]
    url = f"{settings.airflow_url}/api/v1/dags/sap_successfactors_extract/dagRuns"
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
    conn_id: str | None = None,
    target: str = "all",
) -> None:
    from app.services.catalog_service import get_extract_all_plan
    from app.services.extraction_service import run_entity

    entities, skipped = get_extract_all_plan(
        conn_id=conn_id,
        security_context=security_context,
        target=target,
    )
    total = len(entities)
    completed = 0
    failed = 0
    results: list[dict] = []
    skipped_results = list(skipped)

    await _update(
        job_id,
        "running",
        f"Iniciando — {total} entidades scoped en modo {mode}; target={target}; {len(skipped_results)} omitidas",
    )
    await _log(
        job_id,
        None,
        "INFO",
        f"Batch iniciado: {total} entidades scoped, {len(skipped_results)} omitidas, modo={mode}, target={target}",
        {"target": target, "skipped": skipped_results},
    )

    default_concurrency = 1 if conn_id else 4
    try:
        configured_concurrency = int(os.getenv(
            "SAP_SUCCESSFACTORS_EXTRACT_ALL_CONCURRENCY",
            str(default_concurrency),
        ))
    except ValueError:
        configured_concurrency = default_concurrency
    concurrency = max(1, min(configured_concurrency, 4))
    sem = asyncio.Semaphore(concurrency)
    loop = asyncio.get_event_loop()

    async def _one(config: dict) -> None:
        nonlocal completed, failed
        entity = config.get("entity", "?")
        async with sem:
            await _log(job_id, entity, "INFO", "Iniciando extracción")
            try:
                overridden = dict(config)
                overridden["mode"] = mode
                if conn_id:
                    overridden["conn_id"] = conn_id
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
                results.append(classify_successful_extraction({
                    "entity": entity,
                    "record_count": count,
                    "storage_uri": result.get("storage_uri"),
                }))
            except Exception as exc:
                failed += 1
                await _log(job_id, entity, "ERROR", f"Error: {exc}",
                           {"error": str(exc)})
                results.append(classify_extraction_exception(entity, exc))

            done = completed + failed
            await _update(
                job_id, "running",
                f"Progreso {done}/{total} — {completed} OK, {failed} errores",
            )

    await asyncio.gather(*[_one(dict(e)) for e in entities])

    total_records = sum(
        r.get("record_count", 0) for r in results if r["status"] == "extracted"
    )
    result_summary = summarize_extraction_results(results)
    level = "INFO" if failed == 0 else "WARN"
    summary = (
        f"Completado — {completed}/{total} entidades, "
        f"{total_records:,} registros totales, {failed} bloqueadas/fallidas"
    )
    final_status = "failed" if result_summary["failed_open"] else "done"
    await _update(
        job_id, final_status,
        message=summary,
        result={"entities": results, "total_records": total_records,
                "completed": completed, "failed": failed,
                "summary": result_summary,
                "skipped": skipped_results,
                "target": target,
                "selected": total,
                "concurrency": concurrency},
        error="" if final_status == "done" else f"{failed} entities failed-open",
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
