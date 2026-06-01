from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import JSONResponse

from app.api.deps import verify_api_key

from app.core.request_context import reset_security_context, set_security_context
from app.services.catalog_service import get_all_entities, get_entity_config
from app.services.extraction_service import run_entity
from app.services.runlog_service import get_last_run_status
from app.services.watermark_service import list_watermarks
from app.services.kb_service import (
    get_all_knowledge_bits, get_kb_config, run_knowledge_bit,
    run_all_knowledge_bits, get_kb_runs,
)

router = APIRouter(prefix="/skills", tags=["skills"], dependencies=[Depends(verify_api_key)])
_SERVICE = "replicon"


def _security_context(body: dict[str, Any] | None) -> dict[str, Any] | None:
    ctx = body.get("security_context") if isinstance(body, dict) else None
    if not isinstance(ctx, dict) or not ctx.get("trusted"):
        return None
    return ctx


def _run_entity_with_context(
    config: dict[str, Any],
    body: dict[str, Any] | None,
    **kwargs: Any,
) -> dict[str, Any]:
    ctx = _security_context(body)
    token = set_security_context(ctx)
    try:
        scoped_config = {**config, "security_context": ctx} if ctx else config
        return run_entity(scoped_config, **kwargs)
    finally:
        reset_security_context(token)


def _external_failure(exc: Exception, entity: str | None = None) -> JSONResponse:
    response = getattr(exc, "response", None)
    upstream_status = getattr(response, "status_code", None)
    message = "Replicon upstream rejected the request."
    if upstream_status not in (401, 403):
        message = "Replicon upstream request failed."
    payload = {
        "status": "failed",
        "error": "external_request_failed",
        "message": message,
    }
    if entity:
        payload["entity"] = entity
    if upstream_status:
        payload["upstream_status"] = upstream_status
    return JSONResponse(status_code=502, content=payload)


def _humanise_path(path: str) -> str:
    bare = path.split("/skills/", 1)[-1].lstrip("/")
    if not bare:
        return ""
    bare = bare.replace("{", "(").replace("}", ")")
    bare = bare.replace("/", " ").replace("_", " ").strip()
    if not bare:
        return ""
    return bare[0].upper() + bare[1:]


@router.get("/list")
def list_skills() -> dict:
    """Return every skill registered on this cartridge."""
    skills: list[dict] = []
    for route in router.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None) or set()
        if not path or path.endswith("/list"):
            continue
        for method in sorted(methods):
            if method in {"HEAD", "OPTIONS"}:
                continue
            endpoint = getattr(route, "endpoint", None)
            description = ""
            if endpoint and endpoint.__doc__:
                description = endpoint.__doc__.strip().split("\n", 1)[0].strip()
            if not description:
                description = _humanise_path(path)
            skills.append({
                "name": path,
                "method": method,
                "description": description,
                "summary": description,
            })
    return {"service": _SERVICE, "skills": skills}


@router.get("")
def skills_root() -> dict:
    """Protected skill namespace root."""
    return list_skills()


@router.post("/test_connection")
def test_connection() -> dict:
    """Validate Replicon credentials without triggering extraction."""
    try:
        from app.core.replicon_client import RepliconClient
        return RepliconClient().test_connection()
    except Exception as exc:
        return {"status": "error", "message": str(exc)[:200]}


# ------------------------------------------------------------------
# Entity catalogue
# ------------------------------------------------------------------

@router.get("/entities")
def entities() -> dict:
    return {"entities": get_all_entities()}


# ------------------------------------------------------------------
# Extraction
# ------------------------------------------------------------------

@router.post("/run_full_load/{entity}")
def run_full_load(entity: str, body: dict[str, Any] | None = Body(None)) -> dict:
    config = get_entity_config(entity)
    if not config:
        raise HTTPException(status_code=404, detail=f"Entity not found: {entity}")
    try:
        return _run_entity_with_context({**config, "mode": "full"}, body)
    except Exception as exc:
        return _external_failure(exc, entity)


@router.post("/run_incremental/{entity}")
def run_incremental(entity: str, body: dict[str, Any] | None = Body(None)) -> dict:
    config = get_entity_config(entity)
    if not config:
        raise HTTPException(status_code=404, detail=f"Entity not found: {entity}")
    try:
        return _run_entity_with_context({**config, "mode": "incremental"}, body)
    except Exception as exc:
        return _external_failure(exc, entity)


@router.post("/run_full_load_all")
def run_full_load_all(body: dict[str, Any] | None = Body(None)) -> dict:
    results = []
    for config in get_all_entities():
        try:
            results.append(_run_entity_with_context({**config, "mode": "full"}, body))
        except Exception as exc:
            results.append({"entity": config["entity"], "status": "failed", "error": str(exc)})
    return {"results": results}


@router.post("/run_incremental_all")
def run_incremental_all(body: dict[str, Any] | None = Body(None)) -> dict:
    results = []
    for config in get_all_entities():
        mode = "incremental" if config.get("watermark_field") else "full"
        try:
            results.append(_run_entity_with_context({**config, "mode": mode}, body))
        except Exception as exc:
            results.append({"entity": config["entity"], "status": "failed", "error": str(exc)})
    return {"results": results}


@router.post("/run_historical_load/{entity}")
def run_historical_load(
    entity: str,
    from_date: str,
    to_date: str,
    body: dict[str, Any] | None = Body(None),
) -> dict:
    """
    Date-range load for entities with a date_field.
    Uses client-side filtering after full extract.

    Args:
        entity:    table id (e.g. TimeEntry)
        from_date: ISO date start  (e.g. 2020-01-01)
        to_date:   ISO date end    (e.g. 2026-04-04)
    """
    config = get_entity_config(entity)
    if not config:
        raise HTTPException(status_code=404, detail=f"Entity not found: {entity}")
    if not config.get("date_field"):
        raise HTTPException(
            status_code=400,
            detail=f"Entity {entity} has no date_field configured. Use run_full_load instead.",
        )
    try:
        return _run_entity_with_context(
            dict(config),
            body,
            from_date=from_date,
            to_date=to_date,
        )
    except Exception as exc:
        return _external_failure(exc, entity)


@router.post("/run_historical_load_all")
def run_historical_load_all(
    from_date: str,
    to_date: str,
    body: dict[str, Any] | None = Body(None),
) -> dict:
    """Date-range load for all entities that have a date_field."""
    results = []
    for config in get_all_entities():
        if not config.get("date_field"):
            continue
        try:
            results.append(
                _run_entity_with_context(
                    dict(config),
                    body,
                    from_date=from_date,
                    to_date=to_date,
                )
            )
        except Exception as exc:
            results.append({"entity": config["entity"], "status": "failed", "error": str(exc)})
    return {"results": results}


# ------------------------------------------------------------------
# Status / watermarks
# ------------------------------------------------------------------

@router.get("/get_last_run_status")
def last_run_status(entity: str | None = None) -> dict:
    return {"runs": get_last_run_status(entity_name=entity)}


@router.get("/get_watermarks")
def get_watermarks() -> dict:
    return {"watermarks": list_watermarks()}


# ------------------------------------------------------------------
# Table discovery (pass-through to Replicon API)
# ------------------------------------------------------------------

@router.get("/list_tables")
def list_tables() -> dict:
    """Return all available Replicon BI tables with their column schemas."""
    from app.core.replicon_client import RepliconClient
    client = RepliconClient()
    return {"tables": client.list_tables()}


@router.get("/get_table_schema/{table_id}")
def get_table_schema(table_id: str) -> dict:
    from app.core.replicon_client import RepliconClient
    client = RepliconClient()
    return client.get_table_schema(table_id)


# ------------------------------------------------------------------
# Knowledge Bits
# ------------------------------------------------------------------

@router.get("/knowledge_bits")
def knowledge_bits() -> dict:
    return {"knowledge_bits": get_all_knowledge_bits()}


@router.post("/run_knowledge_bits/{kb_id}")
def run_kb(kb_id: str) -> dict:
    config = get_kb_config(kb_id)
    if not config:
        raise HTTPException(status_code=404, detail=f"Knowledge Bit not found: {kb_id}")
    return run_knowledge_bit(kb_id)


@router.post("/run_all_knowledge_bits")
def run_all_kbs() -> dict:
    return {"results": run_all_knowledge_bits()}


@router.get("/get_kb_status")
def kb_status(kb_id: str | None = None) -> dict:
    return {"runs": get_kb_runs(kb_id)}
