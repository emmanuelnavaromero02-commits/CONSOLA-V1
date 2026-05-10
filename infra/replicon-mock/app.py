from __future__ import annotations

import csv
import hashlib
import io
import os
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse


app = FastAPI(title="Replicon Local Mock", version="1.0.0")

MOCK_BASE_URL = "http://replicon-mock:8100"
DEFAULT_USER_COUNT = 1000

MOCK_DATA: dict[str, list[dict[str, Any]]] = {
    "Client": [
        {"client_id": "C001", "name": "Acme Corp", "currency": "USD", "last_modified": "2026-05-01T09:00:00Z"},
        {"client_id": "C002", "name": "Globex", "currency": "EUR", "last_modified": "2026-05-03T09:00:00Z"},
    ],
    "Project": [
        {
            "project_id": "P001",
            "client_id": "C001",
            "name": "ERP Modernization",
            "status": "active",
            "budget_hours": 420,
            "start_date": "2026-04-01",
            "end_date": "2026-09-30",
            "last_modified": "2026-05-04T08:15:00Z",
        },
        {
            "project_id": "P002",
            "client_id": "C002",
            "name": "Analytics Enablement",
            "status": "active",
            "budget_hours": 260,
            "start_date": "2026-05-01",
            "end_date": "2026-08-15",
            "last_modified": "2026-05-05T12:00:00Z",
        },
    ],
    "Task": [
        {"task_id": "T001", "project_id": "P001", "name": "Discovery", "billable": True, "estimated_hours": 40, "last_modified": "2026-05-01T12:00:00Z"},
        {"task_id": "T002", "project_id": "P002", "name": "Dashboard Build", "billable": True, "estimated_hours": 80, "last_modified": "2026-05-02T12:00:00Z"},
    ],
    "TimeEntry": [
        {
            "entry_id": "TE001",
            "user_id": "U001",
            "project_id": "P001",
            "task_id": "T001",
            "activity_id": "A001",
            "date": "2026-05-06",
            "hours": 7.5,
            "billable": True,
            "approved": True,
            "last_modified": "2026-05-06T17:30:00Z",
        },
        {
            "entry_id": "TE002",
            "user_id": "U002",
            "project_id": "P002",
            "task_id": "T002",
            "activity_id": "A001",
            "date": "2026-05-06",
            "hours": 6.0,
            "billable": True,
            "approved": False,
            "last_modified": "2026-05-06T18:00:00Z",
        },
    ],
    "Timesheet": [
        {"timesheet_id": "TS001", "user_id": "U001", "period_start": "2026-05-04", "period_end": "2026-05-10", "status": "approved", "hours": 37.5, "last_modified": "2026-05-10T18:00:00Z"},
        {"timesheet_id": "TS002", "user_id": "U002", "period_start": "2026-05-04", "period_end": "2026-05-10", "status": "submitted", "hours": 32.0, "last_modified": "2026-05-10T18:10:00Z"},
    ],
    "ExpenseEntry": [
        {"expense_id": "E001", "user_id": "U001", "project_id": "P001", "date": "2026-05-05", "category": "Travel", "amount": 180.25, "billable": True, "last_modified": "2026-05-06T09:00:00Z"},
    ],
    "BillingItem": [
        {"billing_item_id": "B001", "project_id": "P001", "user_id": "U001", "date": "2026-05-06", "hours": 7.5, "rate": 120.0, "amount": 900.0, "last_modified": "2026-05-06T18:30:00Z"},
    ],
    "InvoiceItem": [
        {"invoice_item_id": "I001", "billing_item_id": "B001", "project_id": "P001", "amount": 900.0, "currency": "USD", "last_modified": "2026-05-07T10:00:00Z"},
    ],
    "CostItem": [
        {"cost_item_id": "CO001", "project_id": "P001", "user_id": "U001", "date": "2026-05-06", "hours": 7.5, "rate": 65.0, "amount": 487.5, "last_modified": "2026-05-06T18:30:00Z"},
    ],
    "ProfitItem": [
        {"profit_item_id": "PR001", "project_id": "P001", "revenue": 900.0, "cost": 487.5, "profit": 412.5, "last_modified": "2026-05-07T11:00:00Z"},
    ],
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
    "ResourceAllocation": [
        {"allocation_id": "RA001", "project_id": "P001", "user_id": "U001", "role_id": "R002", "hours": 160},
    ],
    "ResourceAssignment": [
        {"assignment_id": "AS001", "project_id": "P001", "user_id": "U001", "role_id": "R002", "allocation_hours": 160, "last_modified": "2026-05-03T10:00:00Z"},
    ],
    "ResourceRequest": [
        {"request_id": "RR001", "project_id": "P002", "role_id": "R001", "hours": 80, "status": "open"},
    ],
    "ProjectTeamMember": [
        {"member_id": "M001", "project_id": "P001", "user_id": "U001", "role_id": "R002"},
        {"member_id": "M002", "project_id": "P002", "user_id": "U002", "role_id": "R001"},
    ],
}

_EXTRACTS: dict[str, str] = {}


def _user_count() -> int:
    raw = os.environ.get("REPLICON_MOCK_USER_COUNT", str(DEFAULT_USER_COUNT))
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = DEFAULT_USER_COUNT
    return max(0, value)


def _user_rows() -> list[dict[str, Any]]:
    departments = ("Engineering", "Finance", "Operations", "Sales", "Customer Success")
    roles = ("Consultant", "Manager", "Analyst", "Architect", "Director")
    statuses = ("active", "active", "active", "inactive")
    rows: list[dict[str, Any]] = []
    for i in range(1, _user_count() + 1):
        rows.append({
            "id": i,
            "userId": f"U{i:06d}",
            "displayName": f"Mock User {i:06d}",
            "email": f"mock.user.{i:06d}@example.test",
            "status": statuses[i % len(statuses)],
            "department": departments[i % len(departments)],
            "role": roles[i % len(roles)],
            "costCenter": f"CC-{(i % 25) + 1:03d}",
            "last_modified": f"2026-05-{(i % 28) + 1:02d}T{(i % 24):02d}:00:00Z",
        })
    return rows


def _rows_for_entity(entity: str) -> list[dict[str, Any]] | None:
    if entity == "User":
        return _user_rows()
    return MOCK_DATA.get(entity)


def _extract_entity(payload: dict[str, Any]) -> str:
    tables = payload.get("tables")
    if not isinstance(tables, list) or not tables:
        raise HTTPException(status_code=400, detail="tables[0].tableId is required")

    table = tables[0]
    if not isinstance(table, dict) or not table.get("tableId"):
        raise HTTPException(status_code=400, detail="tables[0].tableId is required")

    entity = str(table["tableId"])
    if _rows_for_entity(entity) is None:
        raise HTTPException(status_code=404, detail=f"unsupported mock entity: {entity}")
    return entity


def _extract_id(entity: str) -> str:
    digest = hashlib.sha256(entity.encode("utf-8")).hexdigest()[:12]
    return f"mock-{entity.lower()}-{digest}"


def _csv_for_entity(entity: str) -> str:
    rows = _rows_for_entity(entity)
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
    rows = _rows_for_entity(entity)
    if rows is None:
        raise HTTPException(status_code=404, detail=f"unsupported mock entity: {entity}")
    return {"entity": entity, "records": rows}
