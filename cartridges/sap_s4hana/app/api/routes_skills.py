from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import verify_api_key
from app.services.catalog_service import get_all_entities, get_entity_config
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


@router.get("/entities")
def entities() -> dict:
    return {"entities": get_all_entities()}


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
            results.append({"entity": config.get("entity"), "status": "failed", "error": str(exc)})
    return {"results": results}


@router.post("/run_incremental_all")
def run_incremental_all() -> dict:
    results = []
    for config in get_all_entities():
        mode = "incremental" if config.get("watermark_field") else "full"
        try:
            results.append(run_entity({**config, "mode": mode}))
        except Exception as exc:
            results.append({"entity": config.get("entity"), "status": "failed", "error": str(exc)})
    return {"results": results}


@router.post("/run_historical_load/{entity}")
def run_historical_load(entity: str, from_date: str, to_date: str) -> dict:
    config = get_entity_config(entity)
    if not config:
        raise HTTPException(status_code=404, detail=f"Entity not found: {entity}")
    if not config.get("date_field"):
        raise HTTPException(
            status_code=400,
            detail=f"Entity {entity} has no date_field. Use run_full_load instead.",
        )
    return run_entity(dict(config), from_date=from_date, to_date=to_date)


@router.post("/run_historical_load_all")
def run_historical_load_all(from_date: str, to_date: str) -> dict:
    results = []
    for config in get_all_entities():
        if not config.get("date_field"):
            continue
        try:
            results.append(run_entity(dict(config), from_date=from_date, to_date=to_date))
        except Exception as exc:
            results.append({"entity": config.get("entity"), "status": "failed", "error": str(exc)})
    return {"results": results}


@router.get("/get_last_run_status")
def last_run_status(entity: str | None = None) -> dict:
    return {"runs": get_last_run_status(entity_name=entity)}


@router.get("/get_watermarks")
def get_watermarks() -> dict:
    return {"watermarks": list_watermarks()}


@router.get("/list_tables")
def list_tables() -> dict:
    from app.core.sap_client import SapS4Client
    return {"tables": SapS4Client().list_tables()}


@router.get("/get_table_schema/{table_id}")
def get_table_schema(table_id: str) -> dict:
    from app.core.sap_client import SapS4Client
    return SapS4Client().get_table_schema(table_id)


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
