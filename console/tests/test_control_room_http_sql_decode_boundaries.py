from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.routers import control_room
from app.routers import control_room_surfaces as surface_routes
from app.services import control_room_service
from control_room_public_http_harness import DATASET_READER, client
from control_room_surface_fixtures import OPERATOR, snapshot, source_status
from test_control_room_diagnostics_public_boundary import _client as diagnostics_client
from test_control_room_public_sql_decode_boundaries import (
    BUSINESS_SELECT_INSTRUCTIONS,
    PUBLIC_TECHNICAL_CANARIES,
)


def _published_strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [
            item for nested in value.values() for item in _published_strings(nested)
        ]
    if isinstance(value, list):
        return [item for nested in value for item in _published_strings(nested)]
    return []


def _assert_literal_absent(response, literal: str) -> None:
    assert response.status_code == 200
    assert literal not in "\n".join(_published_strings(response.json()))
    assert json.dumps(literal, ensure_ascii=False)[1:-1] not in response.text


@pytest.mark.parametrize("technical", PUBLIC_TECHNICAL_CANARIES)
def test_authenticated_diagnostics_http_omits_sql_and_decode_overflow(
    technical: str,
) -> None:
    collect = AsyncMock(
        return_value=snapshot(
            sources=(source_status(readiness_reason=technical),),
        )
    )
    with patch.object(surface_routes, "collect_surface_snapshot", collect):
        response = diagnostics_client().get("/api/control-room/diagnostics")

    _assert_literal_absent(response, technical)
    collect.assert_awaited_once_with(OPERATOR)


@pytest.mark.parametrize("technical", PUBLIC_TECHNICAL_CANARIES)
def test_authenticated_gold_http_invalidates_sql_and_decode_overflow(
    technical: str,
) -> None:
    control_room._CONTROL_ROOM_READ_CACHE.clear()
    payload = {
        "widgets": [
            {
                "id": "sf_headcount_by_company",
                "title": "Headcount por compañía",
                "value": 1,
                "status": "ready",
                "rows": [{"company_name": technical, "headcount": 1}],
            }
        ]
    }
    with patch.object(
        control_room_service,
        "sap_successfactors_gold_kpis",
        AsyncMock(return_value=payload),
    ):
        response = client(DATASET_READER).get(
            "/api/control-room/sap-successfactors/gold-kpis"
        )

    _assert_literal_absent(response, technical)
    widget = response.json()["widgets"][0]
    assert widget["status"] == "invalid_schema"
    assert widget["value"] is None
    assert widget["rows"] == []


@pytest.mark.parametrize("business_copy", BUSINESS_SELECT_INSTRUCTIONS)
def test_diagnostics_and_gold_http_block_select_shaped_raw_copy(
    business_copy: str,
) -> None:
    collect = AsyncMock(
        return_value=snapshot(
            sources=(source_status(readiness_reason=business_copy),),
        )
    )
    with patch.object(surface_routes, "collect_surface_snapshot", collect):
        diagnostics = diagnostics_client().get("/api/control-room/diagnostics")
    _assert_literal_absent(diagnostics, business_copy)

    control_room._CONTROL_ROOM_READ_CACHE.clear()
    payload = {
        "widgets": [
            {
                "id": "sf_headcount_by_company",
                "title": "Headcount por compañía",
                "value": 1,
                "status": "ready",
                "rows": [{"company_name": business_copy, "headcount": 1}],
            }
        ]
    }
    with patch.object(
        control_room_service,
        "sap_successfactors_gold_kpis",
        AsyncMock(return_value=payload),
    ):
        gold = client(DATASET_READER).get(
            "/api/control-room/sap-successfactors/gold-kpis"
        )
    assert gold.status_code == 200
    widget = gold.json()["widgets"][0]
    assert widget["status"] == "invalid_schema"
    assert widget["value"] is None
    assert widget["rows"] == []
