from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query
from fastapi.responses import JSONResponse

from omega_lakehouse import storage_from_env

from app.api.deps import verify_api_key
from app.security import SecurityContextError, resolve_signed_scope
from app.services.bronze_retention import build_retention_dry_run
from app.services.config_loader import load_company_configs
from app.services.extraction_service import run_company_facts
from app.services.preflight_service import validate_metadata

router = APIRouter(prefix="/skills", tags=["skills"], dependencies=[Depends(verify_api_key)])


@router.get("/list")
def list_skills() -> dict:
    return {
        "service": "sec_edgar",
        "skills": [
            _skill("/skills/test_connection", "POST", "Validate SEC EDGAR metadata and facts preflight."),
            _skill("/skills/run_incremental/company_facts", "POST", "Run incremental SEC EDGAR Bronze ingestion."),
            _skill("/skills/run_full_load/company_facts", "POST", "Run full SEC EDGAR Bronze ingestion."),
            _skill("/skills/retention/sec_bronze_dry_run", "POST", "Report safe SEC EDGAR Bronze cleanup candidates."),
        ],
    }


@router.get("")
def skills_root() -> dict:
    return list_skills()


@router.get("/entities")
def entities() -> dict:
    return {"entities": [{"entity": "company_facts", "mode": "incremental"}, {"entity": "company_metadata", "mode": "full"}]}


@router.get("/get_watermarks")
def get_watermarks() -> dict:
    return {"watermarks": []}


@router.post("/test_connection")
def test_connection(
    conn_id: str | None = Query(default=None, max_length=128),
    x_security_context: str | None = Header(default=None, alias="X-Security-Context"),
) -> JSONResponse:
    try:
        from app.core.sec_client import SECClient

        evidence = validate_metadata(
            SECClient(conn_id=conn_id, security_context=x_security_context),
            load_company_configs(),
        )
        return JSONResponse({"status": "ok", "companies": evidence})
    except Exception as exc:
        return JSONResponse({"status": "error", "error": type(exc).__name__}, status_code=503)


@router.post("/run_incremental/{entity}")
def run_incremental(
    entity: str,
    body: dict[str, Any] | None = Body(None),
    conn_id: str | None = Query(default=None, max_length=128),
    x_security_context: str | None = Header(default=None, alias="X-Security-Context"),
) -> dict:
    return _run(entity, body or {}, mode="incremental", conn_id=conn_id, security_context=x_security_context)


@router.post("/run_full_load/{entity}")
def run_full_load(
    entity: str,
    body: dict[str, Any] | None = Body(None),
    conn_id: str | None = Query(default=None, max_length=128),
    x_security_context: str | None = Header(default=None, alias="X-Security-Context"),
) -> dict:
    return _run(entity, body or {}, mode="full", conn_id=conn_id, security_context=x_security_context)


@router.post("/retention/sec_bronze_dry_run")
def sec_bronze_dry_run(
    body: dict[str, Any] | None = Body(None),
    x_security_context: str | None = Header(default=None, alias="X-Security-Context"),
) -> dict:
    payload = body or {}
    try:
        tenant_id, workspace_id = resolve_signed_scope(x_security_context, payload)
    except SecurityContextError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    try:
        return build_retention_dry_run(
            storage_from_env(),
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            entities=payload.get("entities"),
            keep_latest_per_group=int(payload.get("keep_latest_per_group") or 1),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _run(
    entity: str,
    body: dict[str, Any],
    *,
    mode: str,
    conn_id: str | None,
    security_context: str | None,
) -> dict:
    if entity != "company_facts":
        raise HTTPException(status_code=404, detail=f"Entity not found: {entity}")
    try:
        tenant_id, workspace_id = resolve_signed_scope(security_context, body)
    except SecurityContextError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    try:
        return run_company_facts(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            mode=str(body.get("mode") or mode),
            from_date=body.get("from_date"),
            to_date=body.get("to_date"),
            ciks=body.get("ciks") or body.get("series_ids"),
            run_id=body.get("run_id"),
            conn_id=conn_id or body.get("conn_id"),
            security_context=security_context,
        )
    except Exception as exc:
        return JSONResponse(
            {"status": "failed", "entity": entity, "error": type(exc).__name__},
            status_code=502,
        )


def _skill(name: str, method: str, description: str) -> dict:
    return {"name": name, "method": method, "description": description, "summary": description}
