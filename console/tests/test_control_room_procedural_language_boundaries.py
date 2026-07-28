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
from control_room_public_http_harness import DATASET_READER, client
from control_room_surface_fixtures import OPERATOR, snapshot, source_status
from procedural_language_corpus import (
    PROCEDURAL_LANGUAGE_BUSINESS_COPY,
    PROCEDURAL_LANGUAGE_STATEMENTS,
)
from test_control_room_diagnostics_public_boundary import _client as diagnostics_client


def _gold_widget(label: str) -> dict[str, object]:
    return {
        "id": "sf_headcount_by_company",
        "status": "ok",
        "value": 1,
        "rows": [{"company_name": label, "label": label, "headcount": 1}],
    }


def _assert_absent(payload: object, canary: str) -> None:
    serialized = json.dumps(payload, ensure_ascii=False)
    assert canary not in serialized
    assert json.dumps(canary, ensure_ascii=False)[1:-1] not in serialized


def _assert_literal_absent(raw_json: str, canary: str) -> None:
    assert canary not in raw_json
    assert json.dumps(canary, ensure_ascii=False)[1:-1] not in raw_json


def _diagnostics_direct(copy: str) -> object:
    return build_diagnostics_response(
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
    ).model_dump(mode="json", exclude_none=True)


def _diagnostics_http(copy: str):
    collect = AsyncMock(
        return_value=snapshot(sources=(source_status(readiness_reason=copy),))
    )
    with patch.object(surface_routes, "collect_surface_snapshot", collect):
        response = diagnostics_client().get("/api/control-room/diagnostics")
    collect.assert_awaited_once_with(OPERATOR)
    assert response.status_code == 200
    return response


def _gold_http(copy: str):
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
    return response


@pytest.mark.parametrize("canary", PROCEDURAL_LANGUAGE_STATEMENTS)
def test_diagnostics_direct_omits_procedural_language(canary: str) -> None:
    _assert_absent(_diagnostics_direct(canary), canary)


@pytest.mark.parametrize("canary", PROCEDURAL_LANGUAGE_STATEMENTS)
def test_gold_direct_invalidates_procedural_language(canary: str) -> None:
    widget = _sf_gold_public_widget(_gold_widget(canary))

    _assert_absent(widget, canary)
    assert (widget["status"], widget["value"], widget["rows"]) == (
        "invalid_schema",
        None,
        [],
    )


@pytest.mark.parametrize("canary", PROCEDURAL_LANGUAGE_STATEMENTS)
def test_diagnostics_http_omits_procedural_language(canary: str) -> None:
    response = _diagnostics_http(canary)

    _assert_absent(response.json(), canary)
    _assert_literal_absent(response.content.decode("utf-8"), canary)


@pytest.mark.parametrize("canary", PROCEDURAL_LANGUAGE_STATEMENTS)
def test_gold_http_invalidates_procedural_language(canary: str) -> None:
    response = _gold_http(canary)

    _assert_absent(response.json(), canary)
    _assert_literal_absent(response.content.decode("utf-8"), canary)
    widget = response.json()["widgets"][0]
    assert (widget["status"], widget["value"], widget["rows"]) == (
        "invalid_schema",
        None,
        [],
    )


@pytest.mark.parametrize("copy", PROCEDURAL_LANGUAGE_BUSINESS_COPY)
def test_diagnostics_direct_preserves_procedural_business_copy(copy: str) -> None:
    assert copy in json.dumps(_diagnostics_direct(copy), ensure_ascii=False)


@pytest.mark.parametrize("copy", PROCEDURAL_LANGUAGE_BUSINESS_COPY)
def test_gold_direct_preserves_procedural_business_copy(copy: str) -> None:
    widget = _sf_gold_public_widget(_gold_widget(copy))

    assert (widget["status"], widget["value"]) == ("ready", 1)
    assert widget["rows"][0]["company_name"] == copy
    assert widget["rows"][0]["label"] == copy


@pytest.mark.parametrize("copy", PROCEDURAL_LANGUAGE_BUSINESS_COPY)
def test_diagnostics_http_preserves_procedural_business_copy(copy: str) -> None:
    response = _diagnostics_http(copy)

    assert copy in response.content.decode("utf-8")


@pytest.mark.parametrize("copy", PROCEDURAL_LANGUAGE_BUSINESS_COPY)
def test_gold_http_preserves_procedural_business_copy(copy: str) -> None:
    widget = _gold_http(copy).json()["widgets"][0]

    assert (widget["status"], widget["value"]) == ("ready", 1)
    assert widget["rows"][0]["company_name"] == copy
    assert widget["rows"][0]["label"] == copy
