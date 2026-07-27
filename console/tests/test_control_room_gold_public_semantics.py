from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.routers import control_room
from app.services import control_room_service
from control_room_public_http_harness import DATASET_READER, client


def _get(payload: dict):
    control_room._CONTROL_ROOM_READ_CACHE.clear()
    with patch.object(
        control_room_service,
        "sap_successfactors_gold_kpis",
        AsyncMock(return_value=payload),
    ):
        return client(DATASET_READER).get(
            "/api/control-room/sap-successfactors/gold-kpis"
        )


def test_rejected_partial_count_cannot_remain_in_public_value() -> None:
    response = _get(
        {
            "widgets": [
                {
                    "id": "sf_headcount_by_company",
                    "value": 100,
                    "status": "partial",
                    "_coverage_observed": True,
                    "rows": [
                        {"company_name": "Comercio", "headcount": 90},
                        {"company_name": "gold_private", "headcount": 10},
                    ],
                }
            ]
        }
    )

    assert response.status_code == 200
    widget = response.json()["widgets"][0]
    assert (widget["status"], widget["value"], widget["rows"]) == (
        "invalid_schema",
        None,
        [],
    )
    assert "gold_private" not in response.text


def test_generic_headcount_without_business_label_fails_closed() -> None:
    response = _get(
        {
            "widgets": [
                {
                    "id": "sf_headcount",
                    "value": 10,
                    "status": "ready",
                    "rows": [{"company_id": "technical-id", "headcount": 10}],
                }
            ]
        }
    )

    assert response.status_code == 200
    widget = response.json()["widgets"][0]
    assert (widget["status"], widget["value"], widget["rows"]) == (
        "invalid_schema",
        None,
        [],
    )


@pytest.mark.parametrize(
    "row",
    (
        {"company_id": "COMP-001", "count": 1},
        {"label": "Comercio", "company_name": "Ventas", "count": 1},
        {
            "label": "Comercio",
            "company_name": "Comercio",
            "location_name": "Madrid",
            "count": 1,
        },
    ),
)
def test_generic_metric_requires_one_consistent_business_identity(
    row: dict[str, object],
) -> None:
    response = _get(
        {
            "widgets": [
                {
                    "id": "sf_contractor_risk",
                    "title": "Riesgo de contratistas",
                    "value": 1,
                    "status": "ready",
                    "rows": [row],
                }
            ]
        }
    )

    assert response.status_code == 200
    widget = response.json()["widgets"][0]
    assert (widget["status"], widget["value"], widget["rows"]) == (
        "invalid_schema",
        None,
        [],
    )


@pytest.mark.parametrize(
    ("widget_id", "row"),
    (
        (
            "sf_headcount_by_company",
            {"company_name": "Comercio", "location_name": "Madrid", "headcount": 1},
        ),
        (
            "sf_headcount_by_company",
            {"company_name": "Comercio", "label": "Ventas", "headcount": 1},
        ),
        (
            "sf_headcount_by_location",
            {"location_name": "Madrid", "department_name": "Ventas", "headcount": 1},
        ),
    ),
)
def test_dedicated_dimension_rejects_contradictory_business_identity(
    widget_id: str,
    row: dict[str, object],
) -> None:
    response = _get(
        {
            "widgets": [
                {
                    "id": widget_id,
                    "value": 1,
                    "status": "ready",
                    "rows": [row],
                }
            ]
        }
    )

    assert response.status_code == 200
    widget = response.json()["widgets"][0]
    assert (widget["status"], widget["value"], widget["rows"]) == (
        "invalid_schema",
        None,
        [],
    )


@pytest.mark.parametrize("status", ("missing", "blocked", "empty", "invalid_schema"))
def test_generic_non_ready_headcount_omits_observations(status: str) -> None:
    response = _get(
        {
            "widgets": [
                {
                    "id": "sf_headcount",
                    "value": 10,
                    "status": status,
                    "rows": [{"company_name": "Comercio", "headcount": 10}],
                }
            ]
        }
    )

    assert response.status_code == 200
    widget = response.json()["widgets"][0]
    assert (widget["status"], widget["value"], widget["rows"]) == (
        status,
        None,
        [],
    )


@pytest.mark.parametrize("status", ("missing", "blocked", "empty", "invalid_schema"))
def test_generic_non_ready_headcount_without_rows_omits_value(status: str) -> None:
    response = _get(
        {
            "widgets": [
                {
                    "id": "sf_headcount",
                    "value": 10,
                    "status": status,
                    "rows": [],
                }
            ]
        }
    )

    assert response.status_code == 200
    widget = response.json()["widgets"][0]
    assert (widget["status"], widget["value"], widget["rows"]) == (
        status,
        None,
        [],
    )


def test_unhashable_status_fails_closed_instead_of_raising() -> None:
    response = _get(
        {
            "widgets": [
                {
                    "id": "sf_contractor_risk",
                    "value": 1,
                    "status": [],
                    "rows": [{"label": "gold_private", "count": 1}],
                }
            ]
        }
    )

    assert response.status_code == 200
    widget = response.json()["widgets"][0]
    assert (widget["status"], widget["value"], widget["rows"]) == (
        "invalid_schema",
        None,
        [],
    )


@pytest.mark.parametrize("status", ([], {}, True, 1))
def test_dimension_status_must_be_a_scalar_string(status: object) -> None:
    response = _get(
        {
            "widgets": [
                {
                    "id": "sf_headcount_by_company",
                    "value": 1,
                    "status": status,
                    "rows": [{"company_name": "Comercio", "headcount": 1}],
                }
            ]
        }
    )

    assert response.status_code == 200
    widget = response.json()["widgets"][0]
    assert (widget["status"], widget["value"], widget["rows"]) == (
        "invalid_schema",
        None,
        [],
    )


@pytest.mark.parametrize("generic", (False, True))
@pytest.mark.parametrize("explicit_null", (False, True))
def test_missing_or_null_headcount_status_cannot_infer_ready(
    generic: bool,
    explicit_null: bool,
) -> None:
    widget = {
        "id": "sf_headcount" if generic else "sf_headcount_by_company",
        "value": 1,
        "rows": [{"company_name": "Comercio", "headcount": 1}],
    }
    if explicit_null:
        widget["status"] = None

    response = _get({"widgets": [widget]})

    assert response.status_code == 200
    published = response.json()["widgets"][0]
    assert (published["status"], published["value"], published["rows"]) == (
        "unavailable",
        None,
        [],
    )
