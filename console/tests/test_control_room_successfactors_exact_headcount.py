from __future__ import annotations

from collections.abc import Mapping
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.routers import control_room
from app.services import control_room_service
from app.services.intelligence import successfactors_gold_headcount
from app.services.intelligence.successfactors_active_headcount import (
    ACTIVE_HEADCOUNT_DATASET,
    query_exact_active_headcount,
)


USER = {
    "id": 7,
    "email": "ops@example.com",
    "tenant_id": "tenant-a",
    "active_tenant_id": "tenant-a",
    "workspace_id": "workspace-a",
    "active_workspace_id": "workspace-a",
    "role": "admin",
}
ACTIVE_TABLE = "gold_sap_successfactors_employee_360"
VALID_COLUMNS = {
    ACTIVE_TABLE: {
        "tenant_id": "text",
        "workspace_id": "text",
        "is_active": "boolean",
    }
}


class _AggregateConnection:
    def __init__(self, value: object):
        self.value = value
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    async def fetch(self, sql: str, *args: object):
        self.calls.append((sql, args))
        return [{"active_headcount": self.value}]


@pytest.mark.asyncio
@pytest.mark.parametrize("observed", [100, 0])
async def test_exact_active_aggregate_is_scoped_unlimited_and_keeps_real_zero(
    observed: int,
):
    conn = _AggregateConnection(observed)

    result = await query_exact_active_headcount(
        conn,  # type: ignore[arg-type]
        columns=VALID_COLUMNS,
        table=ACTIVE_TABLE,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
    )

    assert result == {
        "rows": [],
        "total": observed,
        "status": "ready",
        "error": None,
    }
    sql, args = conn.calls[0]
    assert "COUNT(*)::bigint" in sql
    assert "is_active IS TRUE" in sql
    assert "workspace_id::text = $1" in sql
    assert "tenant_id::text = $2" in sql
    assert "LIMIT" not in sql.upper()
    assert args == ("workspace-a", "tenant-a")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("columns", "expected_status"),
    [
        ({}, "missing"),
        (
            {ACTIVE_TABLE: {"workspace_id": "text", "is_active": "boolean"}},
            "invalid_schema",
        ),
        (
            {
                ACTIVE_TABLE: {
                    "tenant_id": "text",
                    "workspace_id": "uuid",
                    "is_active": "boolean",
                }
            },
            "invalid_schema",
        ),
        (
            {
                ACTIVE_TABLE: {
                    "tenant_id": "text",
                    "workspace_id": "text",
                    "is_active": "text",
                }
            },
            "invalid_schema",
        ),
    ],
)
async def test_exact_active_aggregate_rejects_missing_or_invalid_contract(
    columns: Mapping[str, Mapping[str, str]], expected_status: str
):
    conn = _AggregateConnection(7)

    result = await query_exact_active_headcount(
        conn,  # type: ignore[arg-type]
        columns=columns,
        table=ACTIVE_TABLE,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
    )

    assert result["total"] is None
    assert result["status"] == expected_status
    assert conn.calls == []


def _bundle(
    *, active: int | None = 100, active_status: str = "ready", company: int = 90
) -> dict[str, dict]:
    return {
        ACTIVE_HEADCOUNT_DATASET: {
            "rows": [],
            "total": active,
            "status": active_status,
            "error": None if active_status == "ready" else "invalid contract",
        },
        "sap_successfactors_headcount_by_company": {
            "rows": [
                {"company_name": "Comercio", "headcount": company},
                {
                    "company_id": "technical-company-must-not-leak",
                    "company_name": "\u200b",
                    "headcount": 10,
                },
            ],
            "total": company,
            "status": "ready",
            "error": None,
        },
        "sap_successfactors_headcount_by_location": {
            "rows": [{"location_name": "Monterrey", "headcount": 100}],
            "total": 100,
            "status": "ready",
            "error": None,
        },
        "sap_successfactors_headcount_by_department": {
            "rows": [{"department_name": "Personas", "headcount": 100}],
            "total": 100,
            "status": "ready",
            "error": None,
        },
    }


@pytest.mark.asyncio
async def test_service_and_public_endpoint_report_exact_100_with_partial_90(
    monkeypatch,
):
    query = AsyncMock(return_value=_bundle())
    monkeypatch.setattr(
        successfactors_gold_headcount,
        "query_successfactors_headcount_summaries",
        query,
    )
    control_room._CONTROL_ROOM_READ_CACHE.clear()

    service_payload = await control_room_service.sap_successfactors_gold_kpis(USER)
    active, company = service_payload["widgets"][:2]
    assert (active["value"], active["status"]) == (100, "ready")
    assert (company["value"], company["status"]) == (90, "partial")
    assert company["rows"] == [
        {"label": "Comercio", "company_name": "Comercio", "headcount": 90}
    ]

    public = await control_room.control_room_sap_successfactors_gold_kpis(USER)
    payload = public.model_dump()
    public_active, public_company = payload["widgets"][:2]
    assert (public_active["value"], public_active["status"]) == (100, "ready")
    assert (public_company["value"], public_company["status"]) == (90, "partial")
    assert len(public_company["rows"]) == 1
    assert {
        key: public_company["rows"][0][key]
        for key in ("label", "company_name", "headcount")
    } == {"label": "Comercio", "company_name": "Comercio", "headcount": 90}
    assert "technical-company-must-not-leak" not in str(payload)
    assert "\u200b" not in str(payload)


@pytest.mark.asyncio
async def test_service_fails_closed_for_invalid_exact_or_excess_dimension(monkeypatch):
    query = AsyncMock(return_value=_bundle(active=100, company=101))
    monkeypatch.setattr(
        successfactors_gold_headcount,
        "query_successfactors_headcount_summaries",
        query,
    )
    payload = await control_room_service.sap_successfactors_gold_kpis(USER)
    company = payload["widgets"][1]
    assert (company["value"], company["status"], company["rows"]) == (
        None,
        "invalid_schema",
        [],
    )

    query.return_value = _bundle(active=None, active_status="invalid_schema")
    payload = await control_room_service.sap_successfactors_gold_kpis(USER)
    active = payload["widgets"][0]
    assert (active["value"], active["status"]) == (None, "invalid_schema")


@pytest.mark.asyncio
async def test_bundle_rejects_incomplete_user_scope(monkeypatch):
    monkeypatch.setenv("GOLD_DATABASE_URL", "postgresql://gold")
    for user in (
        {"active_workspace_id": "workspace-a"},
        {"active_tenant_id": "  ", "active_workspace_id": "workspace-a"},
        {"active_tenant_id": "tenant-a", "active_workspace_id": "  "},
    ):
        with pytest.raises(HTTPException, match="complete tenant/workspace scope"):
            await (
                successfactors_gold_headcount.query_successfactors_headcount_summaries(
                    user
                )
            )
