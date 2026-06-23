from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query
from fastapi.responses import JSONResponse

from app.api.deps import verify_api_key
from app.core.request_context import SecurityContextError, reset_security_context, set_security_context
from app.services.catalog_service import get_all_entities, get_entity_config, get_extract_all_plan
from app.services.extraction_service import run_entity
from app.services.kb_service import (
    get_all_knowledge_bits,
    get_kb_config,
    get_kb_runs,
    run_all_knowledge_bits,
    run_knowledge_bit,
)
from app.services.runlog_service import get_last_run_status
from app.services.watermark_service import list_watermarks

router = APIRouter(
    prefix="/skills",
    tags=["skills"],
    dependencies=[Depends(verify_api_key)],
)


_SERVICE = "sap_successfactors"


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


def _run_entity_with_context(
    config: dict[str, Any],
    body: dict[str, Any] | None,
    conn_id: str | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    ctx = _security_context(body)
    token = _set_security_context(ctx)
    try:
        scoped_config = {**config, "security_context": ctx} if ctx else config
        if conn_id:
            scoped_config = {**scoped_config, "conn_id": conn_id}
        return run_entity(scoped_config, **kwargs)
    finally:
        reset_security_context(token)


def _run_kb_with_context(kb_id: str, body: dict[str, Any] | None) -> dict:
    ctx = _security_context(body)
    token = _set_security_context(ctx)
    try:
        return run_knowledge_bit(kb_id, ctx)
    finally:
        reset_security_context(token)


def _run_all_kbs_with_context(body: dict[str, Any] | None) -> list[dict]:
    ctx = _security_context(body)
    token = _set_security_context(ctx)
    try:
        return run_all_knowledge_bits(ctx)
    finally:
        reset_security_context(token)


def _extract_all_plan_with_context(
    body: dict[str, Any] | None,
    conn_id: str | None,
    target: str = "all",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    return get_extract_all_plan(
        conn_id=conn_id,
        security_context=_security_context(body),
        target=target,
    )


def _external_failure(exc: Exception, entity: str | None = None) -> JSONResponse:
    response = getattr(exc, "response", None)
    upstream_status = getattr(response, "status_code", None)
    payload = {
        "status": "failed",
        "error": "external_request_failed",
        "message": "SAP SuccessFactors upstream request failed.",
    }
    if entity:
        payload["entity"] = entity
    if upstream_status:
        payload["upstream_status"] = upstream_status
    return JSONResponse(status_code=502, content=payload)


def _humanise_path(path: str) -> str:
    """Backend review P2 fallback — see replicon for rationale."""
    bare = path.split("/skills/", 1)[-1].lstrip("/")
    if not bare:
        return ""
    bare = bare.replace("{", "(").replace("}", ")")
    bare = bare.replace("/", " ").replace("_", " ").strip()
    if not bare:
        return ""
    return bare[0].upper() + bare[1:]


# v1.44.3.3 Task C — GET /skills/list (router-introspecting
# skill discovery; see replicon/sap_hcm for the full rationale).


@router.get("/list")
def list_skills() -> dict:
    """Return every skill registered on this cartridge.

    Generated from the router's own ``routes``; used by the
    console + orchestrator to discover capabilities without
    hardcoding a registry on the caller side."""
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
            # v1.44.3.3 R-Mac-Round-3 Task F: ``description`` is
            # the canonical key (matches orchestrator contract);
            # ``summary`` aliased for one sprint.
            skills.append(
                {
                    "name": path,
                    "method": method,
                    "description": description,
                    "summary": description,  # alias — remove in v1.44.4
                }
            )
    return {"service": _SERVICE, "skills": skills}


@router.get("")
def skills_root() -> dict:
    """Protected skill namespace root."""
    return list_skills()


# v1.41.0 — auditor P1: validate credentials from the console without
# triggering an extraction. SapSfClient.test_connection() is degraded-aware.
@router.post("/test_connection")
def test_connection(
    conn_id: str | None = Query(default=None, max_length=128),
    x_security_context: str | None = Header(default=None, alias="x-security-context"),
) -> dict:
    try:
        from app.core.sap_client import SapSfClient

        return SapSfClient(conn_id=conn_id, security_context=x_security_context).test_connection()
    except Exception as exc:
        return {"status": "error", "message": str(exc)[:200]}


# ── Catalogue ─────────────────────────────────────────────────────────────────


@router.get("/entities")
def entities() -> dict:
    return {"entities": get_all_entities()}


# ── Extraction ────────────────────────────────────────────────────────────────


@router.post("/run_full_load/{entity}")
def run_full_load(
    entity: str,
    conn_id: str | None = Query(default=None, max_length=128),
    body: dict[str, Any] | None = Body(None),
) -> dict:
    config = get_entity_config(entity)
    if not config:
        raise HTTPException(status_code=404, detail=f"Entity not found: {entity}")
    try:
        return _run_entity_with_context({**config, "mode": "full"}, body, conn_id=conn_id)
    except Exception as exc:
        return _external_failure(exc, entity)


@router.post("/run_incremental/{entity}")
def run_incremental(
    entity: str,
    conn_id: str | None = Query(default=None, max_length=128),
    body: dict[str, Any] | None = Body(None),
) -> dict:
    config = get_entity_config(entity)
    if not config:
        raise HTTPException(status_code=404, detail=f"Entity not found: {entity}")
    try:
        return _run_entity_with_context({**config, "mode": "incremental"}, body, conn_id=conn_id)
    except Exception as exc:
        return _external_failure(exc, entity)


@router.post("/run_full_load_all")
def run_full_load_all(
    conn_id: str | None = Query(default=None, max_length=128),
    target: str = Query("all", pattern="^(all|foundation|talent)$"),
    body: dict[str, Any] | None = Body(None),
) -> dict:
    results = []
    entities, skipped = _extract_all_plan_with_context(body, conn_id, target)
    for config in entities:
        try:
            results.append(_run_entity_with_context({**config, "mode": "full"}, body, conn_id=conn_id))
        except Exception as exc:
            results.append(
                {
                    "entity": config.get("entity"),
                    "status": "failed",
                    "error": str(exc),
                }
            )
    return {"target": target, "results": results, "skipped": skipped}


@router.post("/run_incremental_all")
def run_incremental_all(
    conn_id: str | None = Query(default=None, max_length=128),
    target: str = Query("all", pattern="^(all|foundation|talent)$"),
    body: dict[str, Any] | None = Body(None),
) -> dict:
    results = []
    entities, skipped = _extract_all_plan_with_context(body, conn_id, target)
    for config in entities:
        mode = "incremental" if config.get("watermark_field") else "full"
        try:
            results.append(_run_entity_with_context({**config, "mode": mode}, body, conn_id=conn_id))
        except Exception as exc:
            results.append(
                {
                    "entity": config.get("entity"),
                    "status": "failed",
                    "error": str(exc),
                }
            )
    return {"target": target, "results": results, "skipped": skipped}


@router.post("/run_historical_load/{entity}")
def run_historical_load(
    entity: str,
    from_date: str,
    to_date: str,
    conn_id: str | None = Query(default=None, max_length=128),
    body: dict[str, Any] | None = Body(None),
) -> dict:
    config = get_entity_config(entity)
    if not config:
        raise HTTPException(status_code=404, detail=f"Entity not found: {entity}")
    if not config.get("date_field"):
        raise HTTPException(
            status_code=400,
            detail=f"Entity {entity} has no date_field. Use run_full_load instead.",
        )
    try:
        return _run_entity_with_context(
            dict(config),
            body,
            conn_id=conn_id,
            from_date=from_date,
            to_date=to_date,
        )
    except Exception as exc:
        return _external_failure(exc, entity)


@router.post("/run_historical_load_all")
def run_historical_load_all(
    from_date: str,
    to_date: str,
    conn_id: str | None = Query(default=None, max_length=128),
    target: str = Query("all", pattern="^(all|foundation|talent)$"),
    body: dict[str, Any] | None = Body(None),
) -> dict:
    results = []
    entities, skipped = _extract_all_plan_with_context(body, conn_id, target)
    for config in entities:
        if not config.get("date_field"):
            continue
        try:
            results.append(
                _run_entity_with_context(
                    dict(config),
                    body,
                    conn_id=conn_id,
                    from_date=from_date,
                    to_date=to_date,
                )
            )
        except Exception as exc:
            results.append(
                {
                    "entity": config.get("entity"),
                    "status": "failed",
                    "error": str(exc),
                }
            )
    return {"target": target, "results": results, "skipped": skipped}


# ── Status / watermarks ──────────────────────────────────────────────────────


@router.get("/get_last_run_status")
def last_run_status(entity: str | None = None) -> dict:
    return {"runs": get_last_run_status(entity_name=entity)}


@router.get("/get_watermarks")
def get_watermarks() -> dict:
    return {"watermarks": list_watermarks()}


# ── Discovery (real client, no mocks) ────────────────────────────────────────


@router.get("/list_tables")
def list_tables() -> dict:
    from app.core.sap_client import SapSfClient

    return {"tables": SapSfClient().list_tables()}


@router.get("/get_table_schema/{table_id}")
def get_table_schema(table_id: str) -> dict:
    from app.core.sap_client import SapSfClient

    return SapSfClient().get_table_schema(table_id)


# ── Knowledge Bits ───────────────────────────────────────────────────────────


@router.get("/knowledge_bits")
def knowledge_bits() -> dict:
    return {"knowledge_bits": get_all_knowledge_bits()}


@router.post("/run_knowledge_bits/{kb_id}")
def run_kb(kb_id: str, body: dict[str, Any] | None = Body(None)) -> dict:
    config = get_kb_config(kb_id)
    if not config:
        raise HTTPException(status_code=404, detail=f"Knowledge Bit not found: {kb_id}")
    return _run_kb_with_context(kb_id, body)


@router.post("/run_all_knowledge_bits")
def run_all_kbs(body: dict[str, Any] | None = Body(None)) -> dict:
    return {"results": _run_all_kbs_with_context(body)}


@router.get("/get_kb_status")
def kb_status(kb_id: str | None = None) -> dict:
    return {"runs": get_kb_runs(kb_id)}
