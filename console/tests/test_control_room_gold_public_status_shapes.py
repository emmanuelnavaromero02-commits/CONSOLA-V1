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


@pytest.mark.parametrize("generic", (False, True))
@pytest.mark.parametrize(
    "status", ("", "banana", "READY", "OK", " ready ", "ｒｅａｄｙ")
)
def test_unknown_or_noncanonical_headcount_status_fails_closed(
    generic: bool,
    status: str,
) -> None:
    response = _get(
        {
            "widgets": [
                {
                    "id": "sf_headcount" if generic else "sf_headcount_by_company",
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


def test_noncanonical_status_fails_closed_for_generic_non_headcount_widget() -> None:
    response = _get(
        {
            "widgets": [
                {
                    "id": "sf_contractor_risk",
                    "value": 1,
                    "status": " ready ",
                    "rows": [{"label": "Comercio", "count": 1}],
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
@pytest.mark.parametrize(
    "rows",
    (
        [{"company_name": "Comercio", "headcount": 1}, 42],
        [42],
        {"company_name": "Comercio", "headcount": 1},
        "oops",
        None,
    ),
)
def test_malformed_headcount_rows_shape_fails_closed(
    generic: bool,
    rows: object,
) -> None:
    response = _get(
        {
            "widgets": [
                {
                    "id": "sf_headcount" if generic else "sf_headcount_by_company",
                    "value": 1,
                    "status": "ready",
                    "rows": rows,
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


def test_only_active_headcount_can_be_ready_without_business_rows() -> None:
    response = _get(
        {"widgets": [{"id": "sf_headcount", "value": 0, "status": "ready", "rows": []}]}
    )

    assert response.status_code == 200
    widget = response.json()["widgets"][0]
    assert (widget["status"], widget["value"], widget["rows"]) == (
        "invalid_schema",
        None,
        [],
    )


def test_active_headcount_rejects_dimensional_rows() -> None:
    response = _get(
        {
            "widgets": [
                {
                    "id": "sf_active_headcount",
                    "value": 1,
                    "status": "ready",
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


def test_active_headcount_cannot_be_reinterpreted_by_title() -> None:
    response = _get(
        {
            "widgets": [
                {
                    "id": "sf_active_headcount",
                    "title": "sf_headcount_by_company",
                    "value": 1,
                    "status": "ready",
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


@pytest.mark.parametrize(
    ("widget_id", "title", "row"),
    (
        (
            "sf_headcount_by_location",
            "headcount_by_company",
            {"company_name": "Comercio", "headcount": 1},
        ),
        (
            "sf_headcount_by_department",
            "headcount_by_location",
            {"location_name": "Madrid", "headcount": 1},
        ),
    ),
)
def test_exact_dimension_id_cannot_be_reinterpreted_by_title(
    widget_id: str,
    title: str,
    row: dict[str, object],
) -> None:
    response = _get(
        {
            "widgets": [
                {
                    "id": widget_id,
                    "title": title,
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
    ("widget_id", "title"),
    (
        ("evil_sf_active_headcount_shadow", "Headcount"),
        ("sf_headcount", "sf_active_headcount"),
    ),
)
def test_active_headcount_identity_must_match_exact_id(
    widget_id: str,
    title: str,
) -> None:
    response = _get(
        {
            "widgets": [
                {
                    "id": widget_id,
                    "title": title,
                    "value": 100,
                    "status": "ready",
                    "rows": [],
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


def test_active_headcount_partial_never_publishes_value_without_rows() -> None:
    response = _get(
        {
            "widgets": [
                {
                    "id": "sf_active_headcount",
                    "value": 100,
                    "status": "partial",
                    "_coverage_observed": True,
                    "rows": [],
                }
            ]
        }
    )

    assert response.status_code == 200
    widget = response.json()["widgets"][0]
    assert (widget["status"], widget["value"], widget["rows"]) == (
        "partial",
        None,
        [],
    )
