from __future__ import annotations

from decimal import Decimal
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


@pytest.mark.parametrize("project", PROJECTORS)
@pytest.mark.parametrize(
    "status",
    (
        "TABLE payroll",
        "/srv/private/status",
        "password=opaque",
        "Status meeting today",
    ),
)
def test_gold_generic_row_status_accepts_only_typed_runtime_enum(
    project,
    status: str,
) -> None:
    payload = {
        "widgets": [
            {
                "id": "sf_contractor_risk",
                "value": 1,
                "status": "ready",
                "rows": [
                    {
                        "label": "Comercio",
                        "company_name": "Comercio",
                        "count": 1,
                        "status": status,
                    }
                ],
            }
        ]
    }

    result = project(payload)
    widget = result["widgets"][0]
    assert (widget["status"], widget["value"], widget["rows"]) == (
        "invalid_schema",
        None,
        [],
    )
    assert status not in str(result)


@pytest.mark.parametrize("project", PROJECTORS)
@pytest.mark.parametrize("count", (True, 1.0, Decimal("1"), "1", -1, None))
def test_gold_generic_counts_require_explicit_nonnegative_integer(
    project,
    count: object,
) -> None:
    payload = {
        "widgets": [
            {
                "id": "sf_contractor_risk",
                "value": 1,
                "status": "ready",
                "rows": [
                    {
                        "label": "Comercio",
                        "company_name": "Comercio",
                        "count": count,
                    }
                ],
            }
        ]
    }

    widget = project(payload)["widgets"][0]
    assert (widget["status"], widget["value"], widget["rows"]) == (
        "invalid_schema",
        None,
        [],
    )


@pytest.mark.parametrize("project", PROJECTORS)
def test_gold_generic_headcount_partial_is_visible_sum(project) -> None:
    payload = {
        "widgets": [
            {
                "id": "sf_headcount",
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
    assert "TABLE payroll" not in str(widget)


@pytest.mark.parametrize("project", PROJECTORS)
@pytest.mark.parametrize(
    "generated_at",
    (
        "TABLE payroll",
        "/srv/private/status",
        "password=opaque",
        "Quarterly business review",
        "2026-07-28",
        "2026-07-28T12:34:56",
        "2026-02-31T12:34:56Z",
        "２０２６-０７-２８T１２:３４:５６Z",
    ),
)
def test_gold_generated_at_rejects_non_iso_or_non_temporal_text(
    project,
    generated_at: str,
) -> None:
    result = project({"generated_at": generated_at, "widgets": []})

    assert result.get("generated_at") is None
    assert generated_at not in str(result)


@pytest.mark.parametrize("project", PROJECTORS)
@pytest.mark.parametrize(
    "generated_at",
    ("2026-07-28T12:34:56Z", "2026-07-28T12:34:56.123456+02:00"),
)
def test_gold_generated_at_preserves_valid_iso_datetime_bytes(
    project,
    generated_at: str,
) -> None:
    result = project({"generated_at": generated_at, "widgets": []})

    assert result["generated_at"] == generated_at
