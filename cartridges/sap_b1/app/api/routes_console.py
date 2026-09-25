from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import anyio
from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, status
from fastapi.responses import JSONResponse

from app.api.deps import verify_api_key
from app.core.job_runner import (
    _trigger_silver_refresh,
    fail_external_job,
    finish_external_job,
)
from app.core.request_context import (
    SecurityContextError,
    get_security_context,
    require_tenant_workspace_scope,
    reset_security_context,
    scoped_prefix,
    set_security_context,
)
from app.core.b1_source import B1SourceError
from app.services.bronze_parquet import heartbeat_object_name
from app.services.business_parameters import refresh_business_parameters
from app.services.catalog_service import get_all_entities, get_entity_config
from app.services.extraction_service import run_entity
from app.services.intercompany import refresh_intercompany_partners
from app.services.preflight import preflight_for_extract
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


def _require_scope() -> None:
    try:
        require_tenant_workspace_scope()
    except SecurityContextError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def _scoped_config(config: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
    return {**config, "security_context": ctx} if ctx else config


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
        "watermark_ts_field": config.get("watermark_ts_field"),
        "watermark_format": config.get("watermark_format"),
        "primary_key": config.get("primary_key"),
        "parent": config.get("parent"),
        "parent_key": config.get("parent_key"),
        "page_size": config.get("page_size"),
        "select_fields": config.get("select_fields"),
        "date_field": config.get("date_field"),
        "description": config.get("description"),
        "protection": config.get("protection") or {},
    }


@router.get("/entities/{entity_id}/preview")
def entity_preview(
    entity_id: str,
    limit: int = Query(20, ge=1, le=200),
):
    """Preview up to ``limit`` rows of an entity from Bronze (DuckDB → MinIO)."""
    _get_entity_or_404(entity_id)

    try:
        from app.mcp_server import preview as _preview_tool
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


@router.post("/entities/{entity_id}/extract")
def entity_extract(
    entity_id: str,
    mode: str = Query("incremental", pattern="^(full|incremental|historical)$"),
    from_date: str | None = None,
    to_date: str | None = None,
    job_id: str | None = None,
    body: dict[str, Any] | None = Body(None),
):
    """Trigger an extraction for one entity (synchronous, returns when done)."""
    config = _get_entity_or_404(entity_id)
    if mode == "historical" and not config.get("date_field"):
        raise HTTPException(
            status_code=400,
            detail=f"Entity {entity_id} has no date_field — cannot run historical",
        )

    report = preflight_for_extract(_security_context(body))
    if report is not None:
        return _degraded_503(report)

    ctx = _security_context(body)
    token = _set_security_context(ctx)
    try:
        _require_scope()
        result = run_entity(
            _scoped_config({**config, "mode": mode}, ctx),
            from_date=from_date,
            to_date=to_date,
        )
        _mark_external_job(_trigger_silver_refresh, entity_id, ctx)
        _mark_external_job(finish_external_job, job_id, result)
        return result
    except B1SourceError as exc:
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
    body: dict[str, Any] | None = Body(None),
):
    """Run every enabled entity, one after the other."""
    report = preflight_for_extract(_security_context(body))
    if report is not None:
        return _degraded_503(report)

    ctx = _security_context(body)
    token = _set_security_context(ctx)
    try:
        _require_scope()
        results = []
        for config in get_all_entities():
            effective_mode = mode if mode == "full" or config.get("watermark_field") else "full"
            try:
                result = run_entity(_scoped_config({**config, "mode": effective_mode}, ctx))
                _mark_external_job(_trigger_silver_refresh, config.get("entity"), ctx)
                results.append(result)
            except B1SourceError as exc:
                results.append({
                    "entity": config.get("entity"),
                    "status": "degraded",
                    "error": str(exc),
                })
            except Exception as exc:                       # noqa: BLE001
                results.append({
                    "entity": config.get("entity"),
                    "status": "failed",
                    "error": str(exc),
                })
        try:
            result = refresh_intercompany_partners(ctx)
            _mark_external_job(_trigger_silver_refresh, result["entity"], ctx)
            results.append(result)
        except Exception as exc:                           # noqa: BLE001
            results.append({"entity": "IntercompanyPartners", "status": "failed", "error": str(exc)})
        try:
            result = refresh_business_parameters(ctx)
            _mark_external_job(_trigger_silver_refresh, result["entity"], ctx)
            results.append(result)
        except Exception as exc:                           # noqa: BLE001
            results.append({"entity": "BusinessParameters", "status": "failed", "error": str(exc)})
    finally:
        reset_security_context(token)
    return {"results": results}


@router.post("/intercompany/refresh")
def intercompany_refresh(body: dict[str, Any] | None = Body(None)):
    """Write the configured intercompany partner mapping to Bronze."""
    report = preflight_for_extract(_security_context(body))
    if report is not None:
        return _degraded_503(report)
    ctx = _security_context(body)
    token = _set_security_context(ctx)
    try:
        _require_scope()
        result = refresh_intercompany_partners(ctx)
        _mark_external_job(_trigger_silver_refresh, result["entity"], ctx)
        return result
    except B1SourceError as exc:
        return _degraded_503({"status": "degraded", "configured": True, "error": str(exc)})
    finally:
        reset_security_context(token)


@router.post("/business-parameters/refresh")
def business_parameters_refresh(
    refresh_silver: bool = Query(True),
    body: dict[str, Any] | None = Body(None),
):
    """Write the workspace's business parameters (Vault ``business_parameters``) to Bronze."""
    ctx = _security_context(body)
    token = _set_security_context(ctx)
    try:
        _require_scope()
        try:
            result = refresh_business_parameters(ctx)
        except B1SourceError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if refresh_silver:
            _mark_external_job(_trigger_silver_refresh, result["entity"], ctx)
        return result
    finally:
        reset_security_context(token)


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


HEARTBEAT_FIELDS = (
    "schema_version",
    "agent_version",
    "at",
    "source_ok",
    "source_ms",
    "source_error",
    "dialect",
    "companies",
    "last_cycle",
    "initial_load",
    "next_cycle_at",
)


def _read_heartbeat(object_name: str) -> bytes | None:
    from app.core.minio_client import read_object_bytes

    return read_object_bytes(object_name)


def _header_security_context(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        ctx = json.loads(raw)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="security_context is not valid JSON") from exc
    return ctx if isinstance(ctx, dict) else None


@router.get("/connector/status")
def connector_status(
    x_security_context: str | None = Header(default=None, alias="x-security-context"),
) -> dict:
    """Latest heartbeat of the workspace's push agent, with its age."""
    token = _set_security_context(_header_security_context(x_security_context))
    try:
        _require_scope()
        object_name = heartbeat_object_name(scoped_prefix(get_security_context()))
        try:
            raw = _read_heartbeat(object_name)
        except Exception as exc:                               # noqa: BLE001
            return _degraded_503({"status": "degraded", "error": f"heartbeat unavailable ({type(exc).__name__})"})
    finally:
        reset_security_context(token)
    if raw is None:
        return {"present": False}
    try:
        payload = json.loads(raw.decode("utf-8"))
        at = datetime.strptime(str(payload["at"]), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (ValueError, KeyError, TypeError, UnicodeDecodeError, AttributeError):
        return {"present": True, "readable": False, "age_seconds": None}
    age = max(0, int((datetime.now(timezone.utc) - at).total_seconds()))
    return {
        "present": True,
        "readable": True,
        "age_seconds": age,
        **{field: payload.get(field) for field in HEARTBEAT_FIELDS},
    }
