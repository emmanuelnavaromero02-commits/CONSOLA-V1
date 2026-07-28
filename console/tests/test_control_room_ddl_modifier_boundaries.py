from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.routers import control_room
from app.routers import control_room_surfaces as surface_routes
from app.services import control_room_service
from app.services.control_room.diagnostics_public_factory import (
    RawDiagnosticsDraft,
    build_diagnostics_response,
)
from app.services.control_room.successfactors_gold_observations import (
    _sf_gold_public_widget,
)
from app.services.public_sql_sensitivity import contains_public_sql
from app.services.public_sql_statement_scanner import contains_runtime_sql
from app.services.public_text_sensitivity import (
    contains_public_technical_copy,
    public_business_label,
)
from control_room_public_http_harness import DATASET_READER, client
from control_room_surface_fixtures import OPERATOR, snapshot, source_status
from ddl_modifier_corpus import BUSINESS_COPY_CONTROLS
from test_control_room_diagnostics_public_boundary import _client as diagnostics_client


DDL_MODIFIER_CANARIES = (
    "CREATE OR REPLACE SCHEMA Comercio",
    "CREATE TEMP SEQUENCE Comercio",
    "CREATE TEMP MACRO Comercio() AS 1",
    "CREATE TEMPORARY MACRO Comercio() AS 1",
)


def _gold_widget(canary: str) -> dict[str, object]:
    return {
        "id": "sf_headcount_by_company",
        "status": "ok",
        "value": 1,
        "rows": [
            {
                "company_name": canary,
                "label": canary,
                "headcount": 1,
            }
        ],
    }


def _assert_absent(payload: object, canary: str) -> None:
    serialized = json.dumps(payload, ensure_ascii=False)
    assert canary not in serialized
    assert json.dumps(canary, ensure_ascii=False)[1:-1] not in serialized


def _assert_literal_absent(raw_json: str, canary: str) -> None:
    assert canary not in raw_json
    assert json.dumps(canary, ensure_ascii=False)[1:-1] not in raw_json


@pytest.mark.parametrize("canary", DDL_MODIFIER_CANARIES)
def test_ddl_modifier_production_is_blocked_by_direct_policy(canary: str) -> None:
    assert contains_runtime_sql(canary)
    assert contains_public_sql(canary)
    assert contains_public_technical_copy(canary)
    assert public_business_label(canary) is None


@pytest.mark.parametrize("canary", DDL_MODIFIER_CANARIES)
def test_diagnostics_direct_omits_ddl_modifier_production(canary: str) -> None:
    response = build_diagnostics_response(
        RawDiagnosticsDraft(
            generated_at=snapshot().generated_at,
            sources=(
                {
                    "cartridge": "sap_successfactors",
                    "dataset": "employee_360",
                    "status": "ok",
                    "contract_warnings": [canary],
                },
            ),
        )
    )

    _assert_absent(response.model_dump(mode="json", exclude_none=True), canary)


@pytest.mark.parametrize("canary", DDL_MODIFIER_CANARIES)
def test_gold_direct_invalidates_ddl_modifier_production(canary: str) -> None:
    widget = _sf_gold_public_widget(_gold_widget(canary))

    _assert_absent(widget, canary)
    assert (widget["status"], widget["value"], widget["rows"]) == (
        "invalid_schema",
        None,
        [],
    )


@pytest.mark.parametrize("canary", DDL_MODIFIER_CANARIES)
def test_diagnostics_http_omits_ddl_modifier_production(canary: str) -> None:
    collect = AsyncMock(
        return_value=snapshot(
            sources=(source_status(readiness_reason=canary),),
        )
    )
    with patch.object(surface_routes, "collect_surface_snapshot", collect):
        response = diagnostics_client().get("/api/control-room/diagnostics")

    assert response.status_code == 200
    _assert_absent(response.json(), canary)
    _assert_literal_absent(response.content.decode("utf-8"), canary)
    collect.assert_awaited_once_with(OPERATOR)


@pytest.mark.parametrize("canary", DDL_MODIFIER_CANARIES)
def test_gold_http_invalidates_ddl_modifier_production(canary: str) -> None:
    control_room._CONTROL_ROOM_READ_CACHE.clear()
    payload = {"widgets": [_gold_widget(canary)]}
    with patch.object(
        control_room_service,
        "sap_successfactors_gold_kpis",
        AsyncMock(return_value=payload),
    ):
        response = client(DATASET_READER).get(
            "/api/control-room/sap-successfactors/gold-kpis"
        )

    assert response.status_code == 200
    _assert_absent(response.json(), canary)
    _assert_literal_absent(response.content.decode("utf-8"), canary)
    widget = response.json()["widgets"][0]
    assert (widget["status"], widget["value"], widget["rows"]) == (
        "invalid_schema",
        None,
        [],
    )


@pytest.mark.parametrize("copy", BUSINESS_COPY_CONTROLS)
def test_diagnostics_direct_preserves_business_copy(copy: str) -> None:
    response = build_diagnostics_response(
        RawDiagnosticsDraft(
            generated_at=snapshot().generated_at,
            sources=(
                {
                    "cartridge": "sap_successfactors",
                    "dataset": "employee_360",
                    "status": "ok",
                    "contract_warnings": [copy],
                },
            ),
        )
    )

    assert copy in json.dumps(
        response.model_dump(mode="json", exclude_none=True),
        ensure_ascii=False,
    )


@pytest.mark.parametrize("copy", BUSINESS_COPY_CONTROLS)
def test_gold_direct_preserves_business_copy(copy: str) -> None:
    widget = _sf_gold_public_widget(_gold_widget(copy))

    assert (widget["status"], widget["value"]) == ("ready", 1)
    assert widget["rows"][0]["company_name"] == copy
    assert widget["rows"][0]["label"] == copy


@pytest.mark.parametrize("copy", BUSINESS_COPY_CONTROLS)
def test_diagnostics_http_preserves_business_copy(copy: str) -> None:
    collect = AsyncMock(
        return_value=snapshot(
            sources=(source_status(readiness_reason=copy),),
        )
    )
    with patch.object(surface_routes, "collect_surface_snapshot", collect):
        response = diagnostics_client().get("/api/control-room/diagnostics")

    assert response.status_code == 200
    assert copy in response.content.decode("utf-8")
    collect.assert_awaited_once_with(OPERATOR)


@pytest.mark.parametrize("copy", BUSINESS_COPY_CONTROLS)
def test_gold_http_preserves_business_copy(copy: str) -> None:
    control_room._CONTROL_ROOM_READ_CACHE.clear()
    with patch.object(
        control_room_service,
        "sap_successfactors_gold_kpis",
        AsyncMock(return_value={"widgets": [_gold_widget(copy)]}),
    ):
        response = client(DATASET_READER).get(
            "/api/control-room/sap-successfactors/gold-kpis"
        )

    assert response.status_code == 200
    widget = response.json()["widgets"][0]
    assert (widget["status"], widget["value"]) == ("ready", 1)
    assert widget["rows"][0]["company_name"] == copy
    assert widget["rows"][0]["label"] == copy
