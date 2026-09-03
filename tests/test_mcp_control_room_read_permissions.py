from __future__ import annotations

import importlib
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException


REPO = Path(__file__).resolve().parents[1]


def _module(monkeypatch):
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    monkeypatch.setenv("AIRFLOW_USER", "airflow")
    monkeypatch.setenv("AIRFLOW_PASSWORD", "airflow")
    monkeypatch.setenv("PG_PASSWORD", "postgres")
    monkeypatch.setenv("SUPERSET_USER", "admin")
    monkeypatch.setenv("SUPERSET_PASSWORD", "admin")
    monkeypatch.syspath_prepend(str(REPO / "mcp-infra"))
    return importlib.import_module("app.tools.control_room")


def _context(permission: str) -> dict:
    return {
        "trusted": True,
        "tenant_id": "11111111-1111-1111-1111-111111111111",
        "workspace_id": "22222222-2222-2222-2222-222222222222",
        "permissions": [permission],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool_name",
    (
        "control_room__dashboard_read",
        "control_room__ops_summary_read",
        "control_room__alerts_read",
        "control_room__agents_ops_read",
        "control_room__decision_intelligence_runs_read",
    ),
)
async def test_operational_tools_reject_dataset_only_context(monkeypatch, tool_name):
    module = _module(monkeypatch)
    call = AsyncMock(side_effect=AssertionError("console reached"))
    monkeypatch.setattr(module, "_call_console", call)

    with pytest.raises(HTTPException) as error:
        await getattr(module, tool_name)(security_context=_context("datasets.read"))

    assert error.value.status_code == 403
    assert "operations.read" in str(error.value.detail)
    call.assert_not_awaited()


@pytest.mark.asyncio
async def test_business_tool_retains_dataset_read_permission(monkeypatch):
    module = _module(monkeypatch)
    call = AsyncMock(return_value={"data": {"total_anomalies": 0}})
    monkeypatch.setattr(module, "_call_console", call)

    result = await module.control_room__summary_read(
        security_context=_context("datasets.read")
    )

    assert result["data"]["total_anomalies"] == 0
    call.assert_awaited_once()


@pytest.mark.asyncio
async def test_talent_nine_box_tool_never_returns_roster_or_person_fields(monkeypatch):
    module = _module(monkeypatch)
    call = AsyncMock(
        return_value={
            "data": {
                "status": "ready",
                "totals": {"employees": 10, "ready": 8, "blocked": 2, "cells": 9},
                "cells": [
                    {
                        "box_id": "estrella",
                        "employee_count": 8,
                        "movement_action": "promote",
                    }
                ],
                "desempeno_disponible": {
                    "count": 10,
                    "band_counts": {"high": 8, "medium": 2, "low": 0},
                    "roster": [
                        {
                            "employee_key": "tal_abcdef123456",
                            "display_name": "Colaborador 01",
                            "role": "Director",
                            "unit": "People",
                        }
                    ],
                },
            }
        }
    )
    monkeypatch.setattr(module, "_call_console", call)

    result = await module.control_room__talent_9box_read(
        security_context=_context("datasets.read")
    )

    serialized = repr(result["data"])
    assert result["data"]["totals"]["employees"] == 10
    assert result["data"]["desempeno_disponible"]["count"] == 10
    assert "roster" not in result["data"]["desempeno_disponible"]
    for forbidden in (
        "employee_key",
        "display_name",
        "Director",
        "People",
        "movement_action",
    ):
        assert forbidden not in serialized
