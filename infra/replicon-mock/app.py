from __future__ import annotations

import csv
import hashlib
import io
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse


app = FastAPI(title="Replicon Local Mock", version="1.0.0")

MOCK_BASE_URL = "http://replicon-mock:8100"

MOCK_DATA: dict[str, list[dict[str, Any]]] = {
    "Department": [
        {"department_id": "D001", "name": "Engineering", "active": True},
        {"department_id": "D002", "name": "Finance", "active": True},
        {"department_id": "D003", "name": "Operations", "active": True},
    ],
    "Role": [
        {"role_id": "R001", "name": "Consultant"},
        {"role_id": "R002", "name": "Manager"},
    ],
    "Activity": [
        {"activity_id": "A001", "name": "Billable Work"},
        {"activity_id": "A002", "name": "Internal"},
    ],
}

_EXTRACTS: dict[str, str] = {}


def _extract_entity(payload: dict[str, Any]) -> str:
    tables = payload.get("tables")
    if not isinstance(tables, list) or not tables:
        raise HTTPException(status_code=400, detail="tables[0].tableId is required")

    table = tables[0]
    if not isinstance(table, dict) or not table.get("tableId"):
        raise HTTPException(status_code=400, detail="tables[0].tableId is required")

    entity = str(table["tableId"])
    if entity not in MOCK_DATA:
        raise HTTPException(status_code=404, detail=f"unsupported mock entity: {entity}")
    return entity


def _extract_id(entity: str) -> str:
    digest = hashlib.sha256(entity.encode("utf-8")).hexdigest()[:12]
    return f"mock-{entity.lower()}-{digest}"


def _csv_for_entity(entity: str) -> str:
    rows = MOCK_DATA.get(entity)
    if rows is None:
        raise HTTPException(status_code=404, detail=f"unknown extract entity: {entity}")
    if not rows:
        return ""

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/analytics/extracts")
def create_analytics_extract(payload: dict[str, Any]) -> dict[str, str]:
    entity = _extract_entity(payload)
    extract_id = _extract_id(entity)
    _EXTRACTS[extract_id] = entity
    return {"extractId": extract_id}


@app.get("/analytics/extracts/{extract_id}")
def get_analytics_extract(extract_id: str) -> dict[str, Any]:
    entity = _EXTRACTS.get(extract_id)
    if not entity:
        raise HTTPException(status_code=404, detail="extract not found")
    return {
        "extractId": extract_id,
        "status": "completed",
        "dataUrls": {
            entity: f"{MOCK_BASE_URL}/analytics/extracts/{extract_id}/download",
        },
    }


@app.get("/analytics/extracts/{extract_id}/result")
def get_analytics_extract_result(extract_id: str) -> dict[str, Any]:
    return get_analytics_extract(extract_id)


@app.get("/analytics/extracts/{extract_id}/download", response_class=PlainTextResponse)
def download_analytics_extract(extract_id: str) -> PlainTextResponse:
    entity = _EXTRACTS.get(extract_id)
    if not entity:
        raise HTTPException(status_code=404, detail="extract not found")
    return PlainTextResponse(
        _csv_for_entity(entity),
        media_type="text/csv; charset=utf-8",
    )


@app.get("/services/{entity}")
def get_service_entity(entity: str) -> dict[str, Any]:
    if entity not in MOCK_DATA:
        raise HTTPException(status_code=404, detail=f"unsupported mock entity: {entity}")
    return {"entity": entity, "records": MOCK_DATA[entity]}
