"""
Console-style endpoint aliases.

Clean RESTful routes for the MODecissions console UI. They thin-wrap the
existing service functions used by ``/skills/*`` — no logic is duplicated.

Authentication:
    All routes require ``X-Internal-Api-Key`` via ``Depends(verify_api_key)``.

Errors:
    * missing/invalid key       → 401
    * unknown entity            → 404
    * SAP / Postgres / MinIO not configured → 503 with
      ``{status:"degraded", configured:false, missing:[...], components:[...]}``
    * unexpected SAP / runtime  → 503 with the underlying error message
"""
from __future__ import annotations

import anyio
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse

from app.api.deps import verify_api_key
from app.core.extraction_status import (
    classify_extraction_exception,
    classify_successful_extraction,
    summarize_extraction_results,
)
from app.core.job_runner import (
    _trigger_silver_refresh,
    fail_external_job,
    finish_external_job,
)
from app.core.request_context import SecurityContextError, reset_security_context, set_security_context
from app.core.sap_client import SAPClientError, SapSfClient
from app.services.catalog_service import get_all_entities, get_entity_config, get_extract_all_plan
from app.services.extraction_service import run_entity
from app.services.preflight import preflight_for_extract, talent_metadata_readiness
from app.services.runlog_service import get_last_run_status
from app.services.watermark_service import list_watermarks

router = APIRouter(
    tags=["console"],
    dependencies=[Depends(verify_api_key)],
)


def _get_entity_or_404(entity_id: str) -> dict:
    config = get_entity_config(entity_id)
    if not config:
        raise HTTPException(status_code=404, detail=f"Entity not found: {entity_id}")
    return config


def _degraded_503(report: dict) -> JSONResponse:
    return JSONResponse(report, status_code=status.HTTP_503_SERVICE_UNAVAILABLE)


def _mark_external_job(func, *args) -> None:
    try:
        anyio.from_thread.run(func, *args)
    except RuntimeError:
        anyio.run(func, *args)


def _security_context(body: dict[str, Any] | None) -> dict[str, Any] | None:
    ctx = body.get("security_context") if isinstance(body, dict) else None
    if not isinstance(ctx, dict) or not ctx.get("trusted"):
        return None
    return ctx


def _set_security_context(ctx: dict[str, Any] | None):
    try:
        return set_security_context(ctx)
    except SecurityContextError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def _scoped_config(config: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
    return {**config, "security_context": ctx} if ctx else config


def _with_optional_conn(config: dict[str, Any], conn_id: str | None) -> dict[str, Any]:
    selected = (conn_id or "").strip()
    if not selected:
        return config
    return {**config, "conn_id": selected}


def _header_security_context(request: Request) -> str | None:
    value = request.headers.get("x-security-context") or ""
    return value.strip() or None


# ── Catalogue ────────────────────────────────────────────────────────────────

@router.get("/entities")
def entities() -> dict:
    """List every configured entity for this cartridge."""
    return {"entities": get_all_entities()}


@router.get("/entities/{entity_id}/schema")
def entity_schema(entity_id: str) -> dict:
    """Return the configuration / schema for one entity."""
    config = _get_entity_or_404(entity_id)
    return {
        "entity": config.get("entity"),
        "mode": config.get("mode"),
        "watermark_field": config.get("watermark_field"),
        "watermark_format": config.get("watermark_format"),
        "page_size": config.get("page_size"),
        "select_fields": config.get("select_fields"),
        "effective_dated": config.get("effective_dated"),
        "date_field": config.get("date_field"),
        "description": config.get("description"),
        "protection": config.get("protection") or {},
    }


@router.get("/diagnostics/config")
def diagnostics_config(
    request: Request,
    conn_id: str | None = Query(default=None, max_length=128),
) -> dict:
    """Return sanitized effective SuccessFactors config for the live process."""
    client = SapSfClient(conn_id=conn_id, security_context=_header_security_context(request))
    return client.sanitized_config_diagnostics()


@router.get("/talent/metadata-readiness")
def talent_metadata_readiness_probe(
    request: Request,
    conn_id: str | None = Query(default=None, max_length=128),
    sample: bool = Query(default=True),
) -> dict:
    """Validate live SuccessFactors metadata/permission readiness for WB-TALENTO C/P/A."""
    return talent_metadata_readiness(
        conn_id=conn_id,
        security_context=_header_security_context(request),
        sample=sample,
    )


# ── Preview ──────────────────────────────────────────────────────────────────

@router.get("/entities/{entity_id}/preview")
def entity_preview(
    entity_id: str,
    limit: int = Query(20, ge=1, le=200),
):
    """Preview up to ``limit`` rows of an entity from Bronze (DuckDB → MinIO).

    Returns 503 ``{status:"degraded"}`` when MinIO/Bronze can't be reached
    instead of bubbling a 500.
    """
    _get_entity_or_404(entity_id)

    try:
        from app.mcp_server import preview as _preview_tool  # FastMCP @tool
    except Exception as exc:                                   # noqa: BLE001
        return _degraded_503({
            "status": "degraded",
            "error": f"preview unavailable: {exc}",
        })
    try:
        return _preview_tool(entity=entity_id, limit=limit)
    except Exception as exc:                                   # noqa: BLE001
        return _degraded_503({
            "status": "degraded",
            "error": f"preview failed: {exc}",
        })


# ── Extract ──────────────────────────────────────────────────────────────────

@router.post("/entities/{entity_id}/extract")
def entity_extract(
    entity_id: str,
    mode: str = Query("incremental", pattern="^(full|incremental|historical)$"),
    conn_id: str | None = Query(default=None, max_length=128),
    from_date: str | None = None,
    to_date: str | None = None,
    job_id: str | None = None,
    body: dict[str, Any] | None = Body(None),
):
    """Trigger an extraction for one entity (synchronous, returns when done).

    For background batch execution use the MCP ``extract`` tool which
    persists a job in PostgreSQL — this endpoint is the synchronous variant.
    """
    config = _get_entity_or_404(entity_id)
    if mode == "historical" and not config.get("date_field"):
        raise HTTPException(
            status_code=400,
            detail=f"Entity {entity_id} has no date_field — cannot run historical",
        )

    ctx = _security_context(body)
    report = preflight_for_extract(conn_id=conn_id, security_context=ctx)
    if report is not None:
        return _degraded_503(report)

    token = _set_security_context(ctx)
    try:
        result = run_entity(
            _with_optional_conn(_scoped_config({**config, "mode": mode}, ctx), conn_id),
            from_date=from_date,
            to_date=to_date,
        )
        _mark_external_job(_trigger_silver_refresh, entity_id, ctx)
        _mark_external_job(finish_external_job, job_id, result)
        return result
    except SAPClientError as exc:
        _mark_external_job(fail_external_job, job_id, str(exc))
        return _degraded_503({
            "status": "degraded",
            "configured": True,
            "error": str(exc),
        })
    except Exception as exc:
        _mark_external_job(fail_external_job, job_id, str(exc))
        raise
    finally:
        reset_security_context(token)


@router.post("/extract-all")
def extract_all(
    mode: str = Query("incremental", pattern="^(full|incremental)$"),
    target: str = Query("all", pattern="^(all|foundation|talent)$"),
    conn_id: str | None = Query(default=None, max_length=128),
    body: dict[str, Any] | None = Body(None),
):
    """Run every enabled entity, one after the other."""
    ctx = _security_context(body)
    report = preflight_for_extract(conn_id=conn_id, security_context=ctx)
    if report is not None:
        return _degraded_503(report)

    token = _set_security_context(ctx)
    try:
        results = []
        entities, skipped = get_extract_all_plan(
            conn_id=conn_id,
            security_context=ctx,
            target=target,
        )
        for config in entities:
            effective_mode = mode if mode == "full" or config.get("watermark_field") else "full"
            try:
                result = run_entity(
                    _with_optional_conn(_scoped_config({**config, "mode": effective_mode}, ctx), conn_id)
                )
                _mark_external_job(_trigger_silver_refresh, config.get("entity"), ctx)
                results.append(classify_successful_extraction(result))
            except SAPClientError as exc:
                results.append(classify_extraction_exception(config.get("entity"), exc))
            except Exception as exc:                       # noqa: BLE001
                results.append(classify_extraction_exception(config.get("entity"), exc))
    finally:
        reset_security_context(token)
    summary = summarize_extraction_results(results)
    status_text = "success" if not any(
        summary[key] for key in ("auth_blocked", "permission_blocked", "failed_open")
    ) else "completed_with_blocks"
    return {
        "status": status_text,
        "target": target,
        "summary": summary,
        "results": results,
        "skipped": skipped,
    }


# ── Observability ────────────────────────────────────────────────────────────

@router.get("/runs")
def runs(entity: str | None = None) -> dict:
    """Last extraction runs, optionally filtered by entity."""
    return {"runs": get_last_run_status(entity_name=entity)}


@router.get("/runs/latest")
def runs_latest() -> dict:
    """Most recent run record (any entity), or ``None`` when there are none yet."""
    rows = get_last_run_status()
    return {"run": rows[0] if rows else None}


@router.get("/watermarks")
def watermarks() -> dict:
    """All per-entity watermarks tracked by this cartridge."""
    return {"watermarks": list_watermarks()}
