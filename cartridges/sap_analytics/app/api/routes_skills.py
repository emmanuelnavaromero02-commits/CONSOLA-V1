from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.security import verify_api_key
from app.services.catalog_service import get_all_entities, get_entity_config
from app.services.extraction_service import run_entity
from app.services.runlog_service import get_last_run_status
from app.services.watermark_service import list_watermarks
from app.services.kb_service import (
    get_all_knowledge_bits, get_kb_config, run_knowledge_bit,
    run_all_knowledge_bits, get_kb_runs,
)

router = APIRouter(prefix="/skills", tags=["skills"], dependencies=[Depends(verify_api_key)])


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
def run_full_load(entity: str) -> dict:
    config = get_entity_config(entity)
    if not config:
        raise HTTPException(status_code=404, detail=f"Entity not found: {entity}")
    return run_entity({**config, "mode": "full"})


@router.post("/run_incremental/{entity}")
def run_incremental(entity: str) -> dict:
    config = get_entity_config(entity)
    if not config:
        raise HTTPException(status_code=404, detail=f"Entity not found: {entity}")
    return run_entity({**config, "mode": "incremental"})


@router.post("/run_full_load_all")
def run_full_load_all() -> dict:
    results = []
    for config in get_all_entities():
        try:
            results.append(run_entity({**config, "mode": "full"}))
        except Exception as exc:
            results.append({"entity": config["entity"], "status": "failed", "error": str(exc)})
    return {"results": results}


@router.post("/run_incremental_all")
def run_incremental_all() -> dict:
    results = []
    for config in get_all_entities():
        mode = "incremental" if config.get("watermark_field") else "full"
        try:
            results.append(run_entity({**config, "mode": mode}))
        except Exception as exc:
            results.append({"entity": config["entity"], "status": "failed", "error": str(exc)})
    return {"results": results}


@router.post("/run_historical_load/{entity}")
def run_historical_load(entity: str, from_date: str, to_date: str) -> dict:
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
    return run_entity(dict(config), from_date=from_date, to_date=to_date)


@router.post("/run_historical_load_all")
def run_historical_load_all(from_date: str, to_date: str) -> dict:
    """Date-range load for all entities that have a date_field."""
    results = []
    for config in get_all_entities():
        if not config.get("date_field"):
            continue
        try:
            results.append(run_entity(dict(config), from_date=from_date, to_date=to_date))
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
