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
import hashlib
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse

from app.api.deps import verify_api_key
from app.core.extraction_status import (
    classify_extraction_exception,
    classify_successful_extraction,
    public_failure_message,
    summarize_extraction_results,
)
from app.core.job_runner import (
    _trigger_successfactors_gold_refresh,
    _trigger_silver_refresh,
    fail_external_job,
    finish_external_job,
)
from app.core.request_context import SecurityContextError, reset_security_context, set_security_context
from app.core.sap_client import SAPClientError, SapSfClient
from app.services.catalog_service import get_all_entities, get_entity_config, get_extract_all_plan
from app.services.extraction_service import (
    is_metadata_skip_result,
    run_entity_with_metadata_guard as run_entity,
)
from app.services.preflight import (
    people_master_readiness,
    preflight_for_extract,
    talent_metadata_readiness,
)
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


def _entity_idempotency_key(base_key: str | None, entity: object) -> str | None:
    base = str(base_key or "").strip()
    entity_name = str(entity or "").strip()
    if not (base and entity_name):
        return None
    candidate = f"{base}:{entity_name}"
    if len(candidate) <= 180:
        return candidate
    digest = hashlib.sha256(candidate.encode("utf-8")).hexdigest()[:24]
    return f"{base[:120]}:{digest}"


def _degraded_503(report: dict) -> JSONResponse:
    return JSONResponse(report, status_code=status.HTTP_503_SERVICE_UNAVAILABLE)


def _mark_external_job(func, *args) -> Any:
    try:
        return anyio.from_thread.run(func, *args)
    except RuntimeError:
        return anyio.run(func, *args)


def _security_context(body: dict[str, Any] | None) -> dict[str, Any] | None:
    ctx = body.get("security_context") if isinstance(body, dict) else None
    if not isinstance(ctx, dict) or not ctx.get("trusted"):
        return None
    return ctx


def _set_security_context(ctx: dict[str, Any] | None):
    try:
        return set_security_context(ctx)
    except SecurityContextError:
        raise HTTPException(status_code=403, detail="security_context_denied") from None


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


@router.get("/foundation/metadata-readiness")
def foundation_metadata_readiness_probe(
    request: Request,
    conn_id: str | None = Query(default=None, max_length=128),
    sample: bool = Query(default=True),
) -> dict:
    """Read-only people-master (Employee Central) OData permission checklist.

    Per-entity verdict (User/EmpEmployment/EmpJob required; PerPersonal/PerPerson/
    FOJobCode/Position optional) so the owner knows exactly which OData read grants
    to request from the SAP admin to unblock the SF golden path.
    """
    return people_master_readiness(
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
    except Exception:                                          # noqa: BLE001
        return _degraded_503({
            "status": "degraded",
            "error": "preview_unavailable",
        })
    try:
        return _preview_tool(entity=entity_id, limit=limit)
    except Exception:                                          # noqa: BLE001
        return _degraded_503({
            "status": "degraded",
            "error": "preview_failed",
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

    request_conn_id = conn_id if isinstance(conn_id, str) else None
    selected_conn_id = (
        str(request_conn_id or config.get("conn_id") or config.get("connection_id") or "").strip()
        or None
    )
    ctx = _security_context(body)
    report = preflight_for_extract(conn_id=selected_conn_id, security_context=ctx)
    if report is not None:
        return _degraded_503(report)

    token = _set_security_context(ctx)
    try:
        result = run_entity(
            _with_optional_conn(
                _scoped_config({**config, "mode": mode}, ctx),
                selected_conn_id,
            ),
            from_date=from_date,
            to_date=to_date,
        )
        if is_metadata_skip_result(result):
            _mark_external_job(finish_external_job, job_id, result)
            return result
        _mark_external_job(_trigger_silver_refresh, entity_id, ctx)
        _mark_external_job(finish_external_job, job_id, result)
        return result
    except Exception as exc:  # noqa: BLE001 - expose only the stable classifier.
        classified = classify_extraction_exception(entity_id, exc)
        failure_message = public_failure_message(classified)
        _mark_external_job(fail_external_job, job_id, failure_message)
        return _degraded_503(
            {
                "status": "degraded",
                "configured": True,
                **classified,
            }
        )
    finally:
        reset_security_context(token)


@router.post("/extract-all")
def extract_all(
    mode: str = Query("incremental", pattern="^(full|incremental)$"),
    target: str = Query("all", pattern="^(all|foundation|talent)$"),
    conn_id: str | None = Query(default=None, max_length=128),
    idempotency_key: str | None = Query(default=None, max_length=180),
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
        results.extend(
            {
                "entity": item.get("entity"),
                "status": "skipped_explicit" if item.get("status") == "skipped" else item.get("status", "blocked"),
                "reason": item.get("reason") or "unknown_error",
                "code": item.get("code") or "PLAN_OUTCOME",
                "metadata_status": item.get("metadata_status"),
                "fields_missing": item.get("fields_missing") or [],
            }
            for item in skipped
        )
        for config in entities:
            effective_mode = mode if mode == "full" or config.get("watermark_field") else "full"
            entity_idempotency_key = _entity_idempotency_key(
                idempotency_key, config.get("entity")
            )
            run_config = {**config, "mode": effective_mode}
            if entity_idempotency_key:
                run_config["idempotency_key"] = entity_idempotency_key
                run_config["parent_idempotency_key"] = idempotency_key
            try:
                result = run_entity(
                    _with_optional_conn(_scoped_config(run_config, ctx), conn_id)
                )
                if is_metadata_skip_result(result):
                    results.append(result)
                    continue
                _mark_external_job(_trigger_silver_refresh, config.get("entity"), ctx)
                results.append(classify_successful_extraction(result))
            except SAPClientError as exc:
                results.append(classify_extraction_exception(config.get("entity"), exc))
            except Exception as exc:                       # noqa: BLE001
                results.append(classify_extraction_exception(config.get("entity"), exc))
    finally:
        reset_security_context(token)
    summary = summarize_extraction_results(results)
    gold_refresh = None
    if any(isinstance(item, dict) and item.get("status") in {"extracted", "partial"} for item in results):
        gold_refresh = _mark_external_job(
            _trigger_successfactors_gold_refresh,
            target,
            ctx,
        )
    status_text = "success" if not any(
        summary[key]
        for key in (
            "auth_blocked",
            "permission_blocked",
            "failed_open",
            "blocked",
            "skipped_explicit",
            "partial",
        )
    ) else "completed_with_blocks"
    attempted = [
        item for item in results
        if item.get("entity") and not str(item.get("entity")).startswith("__")
    ]
    return {
        "status": status_text,
        "target": target,
        "attempted": len(attempted),
        "triggered": [
            item for item in results
            if item.get("status") in {"extracted", "empty-valid", "partial"}
        ],
        "blocked": [item for item in results if item.get("status") == "blocked"],
        "partial": [item for item in results if item.get("status") == "partial"],
        "failed": [
            item for item in results
            if item.get("status") in {"failed", "failed-open", "auth-blocked", "permission-blocked"}
        ],
        "skipped_explicit": [item for item in results if item.get("status") == "skipped_explicit"],
        "summary": summary,
        "results": results,
        "skipped": skipped,
        "outcomes": skipped,
        "gold_refresh": gold_refresh,
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
