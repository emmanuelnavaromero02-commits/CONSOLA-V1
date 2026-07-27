from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.routers import control_room
from app.routers import control_room_surfaces as surface_routes
from app.services import control_room_service
from control_room_public_http_harness import DATASET_READER, client
from control_room_surface_fixtures import OPERATOR, snapshot, source_status
from test_control_room_diagnostics_public_boundary import _client as diagnostics_client
from test_control_room_sql_business_instruction_boundaries import (
    BUSINESS_SELECT_INSTRUCTIONS,
    HIDDEN_QUERY_SUFFIXES,
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


def _diagnostics_response(value: str):
    collect = AsyncMock(
        return_value=snapshot(
            sources=(source_status(readiness_reason=value),),
        )
    )
    with patch.object(surface_routes, "collect_surface_snapshot", collect):
        response = diagnostics_client().get("/api/control-room/diagnostics")
    collect.assert_awaited_once_with(OPERATOR)
    return response


def _gold_response(value: str):
    control_room._CONTROL_ROOM_READ_CACHE.clear()
    payload = {
        "widgets": [
            {
                "id": "sf_headcount_by_company",
                "title": "Headcount por compañía",
                "value": 1,
                "status": "ready",
                "rows": [{"company_name": value, "headcount": 1}],
            }
        ]
    }
    with patch.object(
        control_room_service,
        "sap_successfactors_gold_kpis",
        AsyncMock(return_value=payload),
    ):
        return client(DATASET_READER).get(
            "/api/control-room/sap-successfactors/gold-kpis"
        )


@pytest.mark.parametrize("query", HIDDEN_QUERY_SUFFIXES)
def test_authenticated_diagnostics_http_blocks_hidden_query(query: str) -> None:
    diagnostics = _diagnostics_response(query)
    assert diagnostics.status_code == 200
    assert query not in _published_strings(diagnostics.json())


@pytest.mark.parametrize("query", HIDDEN_QUERY_SUFFIXES)
def test_authenticated_gold_http_blocks_hidden_query(
    query: str,
) -> None:
    gold = _gold_response(query)
    assert gold.status_code == 200
    assert query not in _published_strings(gold.json())
    widget = gold.json()["widgets"][0]
    assert widget["status"] == "invalid_schema"
    assert widget["value"] is None
    assert widget["rows"] == []


@pytest.mark.parametrize("business_copy", BUSINESS_SELECT_INSTRUCTIONS)
def test_authenticated_diagnostics_http_preserves_business_instruction_byte_exact(
    business_copy: str,
) -> None:
    diagnostics = _diagnostics_response(business_copy)
    assert diagnostics.status_code == 200
    assert business_copy in _published_strings(diagnostics.json())


@pytest.mark.parametrize("business_copy", BUSINESS_SELECT_INSTRUCTIONS)
def test_authenticated_gold_http_preserves_business_instruction_byte_exact(
    business_copy: str,
) -> None:
    gold = _gold_response(business_copy)
    assert gold.status_code == 200
    widget = gold.json()["widgets"][0]
    assert widget["status"] == "ready"
    assert widget["value"] == 1
    assert widget["rows"][0]["label"] == business_copy
