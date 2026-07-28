from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.routers import control_room
from app.schemas.control_room_legacy_responses import ControlRoomGoldKpisResponse
from app.services import control_room_service
from app.services.control_room.successfactors_gold_public_factory import (
    project_public_gold_response,
)
from control_room_public_http_harness import DATASET_READER, client


def _factory(payload: dict) -> dict:
    return project_public_gold_response(payload)


def _direct(payload: dict) -> dict:
    return ControlRoomGoldKpisResponse.project(payload).model_dump()


def _http(payload: dict) -> dict:
    control_room._CONTROL_ROOM_READ_CACHE.clear()
    with patch.object(
        control_room_service,
        "sap_successfactors_gold_kpis",
        AsyncMock(return_value=payload),
    ):
        response = client(DATASET_READER).get(
            "/api/control-room/sap-successfactors/gold-kpis"
        )
    assert response.status_code == 200
    return response.json()


PROJECTORS = (_factory, _direct, _http)


def _widget(row: dict[str, object]) -> dict:
    return {
        "widgets": [
            {
                "id": "sf_contractor_risk",
                "value": 1,
                "status": "ready",
                "rows": [row],
            }
        ]
    }


@pytest.mark.parametrize("project", PROJECTORS)
def test_generic_widget_restores_safe_label_only_identity(project) -> None:
    result = project(_widget({"label": "Comercio", "count": 1}))

    widget = result["widgets"][0]
    assert (widget["status"], widget["value"]) == ("ready", 1)
    assert len(widget["rows"]) == 1
    assert widget["rows"][0]["label"] == "Comercio"
    assert widget["rows"][0]["count"] == 1


@pytest.mark.parametrize("project", PROJECTORS)
@pytest.mark.parametrize(
    "name_key", ("company_name", "location_name", "department_name", "fact")
)
def test_generic_widget_accepts_one_name_matching_label(project, name_key: str) -> None:
    result = project(_widget({"label": "Comercio", name_key: "Comercio", "count": 1}))

    widget = result["widgets"][0]
    assert (widget["status"], widget["value"]) == ("ready", 1)
    assert widget["rows"][0]["label"] == "Comercio"
    assert widget["rows"][0][name_key] == "Comercio"


@pytest.mark.parametrize("project", PROJECTORS)
@pytest.mark.parametrize(
    "row",
    (
        {"label": "Comercio", "company_name": "Ventas", "count": 1},
        {
            "label": "Comercio",
            "company_name": "Comercio",
            "location_name": "Comercio",
            "count": 1,
        },
        {"company_name": "Comercio", "location_name": "Comercio", "count": 1},
    ),
)
def test_generic_widget_rejects_contradictory_or_multiple_names(
    project, row: dict[str, object]
) -> None:
    result = project(_widget(row))

    widget = result["widgets"][0]
    assert (widget["status"], widget["value"], widget["rows"]) == (
        "invalid_schema",
        None,
        [],
    )


@pytest.mark.parametrize("project", PROJECTORS)
@pytest.mark.parametrize(
    "label",
    (
        "TABLE payroll",
        "/srv/private/payroll.csv",
        "password=opaque-secret",
        "workspace_id",
        "(sin nombre)",
    ),
)
def test_generic_label_only_never_bypasses_public_text_policy(
    project, label: str
) -> None:
    result = project(_widget({"label": label, "count": 1}))

    widget = result["widgets"][0]
    assert (widget["status"], widget["value"], widget["rows"]) == (
        "invalid_schema",
        None,
        [],
    )
    assert label not in str(result)


@pytest.mark.parametrize("project", PROJECTORS)
def test_generic_label_compatibility_does_not_change_sf_dimension_totals(
    project,
) -> None:
    payload = {
        "widgets": [
            {
                "id": "sf_headcount_by_company",
                "value": 100,
                "status": "ready",
                "rows": [
                    {"company_name": "Comercio", "headcount": 90},
                    {"company_name": "TABLE payroll", "headcount": 10},
                ],
            }
        ]
    }

    widget = project(payload)["widgets"][0]
    assert (widget["status"], widget["value"]) == ("partial", 90)
    assert len(widget["rows"]) == 1
    assert widget["rows"][0]["company_name"] == "Comercio"
    assert widget["rows"][0]["headcount"] == 90
    assert "TABLE payroll" not in str(widget)
