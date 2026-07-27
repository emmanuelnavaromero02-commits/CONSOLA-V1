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


@pytest.mark.parametrize(
    ("widget_id", "row"),
    (
        ("sf_active_headcount", None),
        ("sf_headcount", {"company_name": "Comercio", "headcount": 1}),
        (
            "sf_headcount_by_company",
            {"company_name": "Comercio", "headcount": 1},
        ),
        (
            "sf_headcount_by_location",
            {"location_name": "Madrid", "headcount": 1},
        ),
        (
            "sf_headcount_by_department",
            {"department_name": "Ventas", "headcount": 1},
        ),
    ),
)
def test_canonical_gold_widget_identity_survives_public_projection(
    widget_id: str,
    row: dict[str, object] | None,
) -> None:
    response = _get(
        {
            "widgets": [
                {
                    "id": widget_id,
                    "value": 1,
                    "status": "ready",
                    "rows": [] if row is None else [row],
                }
            ]
        }
    )

    assert response.status_code == 200
    widget = response.json()["widgets"][0]
    assert (widget["id"], widget["status"], widget["value"]) == (
        widget_id,
        "ready",
        1,
    )


@pytest.mark.parametrize(
    ("widget_id", "row"),
    (
        (None, {"company_name": "Comercio", "headcount": 1}),
        ("", {"company_name": "Comercio", "headcount": 1}),
        ([], {"company_name": "Comercio", "headcount": 1}),
        ({}, {"company_name": "Comercio", "headcount": 1}),
        (" sf_active_headcount ", {"company_name": "Comercio", "headcount": 1}),
        (
            "ｓｆ＿ａｃｔｉｖｅ＿ｈｅａｄｃｏｕｎｔ",
            {"company_name": "Comercio", "headcount": 1},
        ),
        (
            "ｓｆ＿ｈｅａｄｃｏｕｎｔ＿ｂｙ＿ｌｏｃａｔｉｏｎ",
            {"company_name": "Comercio", "headcount": 1},
        ),
        ("headcount_by_company", {"company_name": "Comercio", "headcount": 1}),
        (
            "evil_headcount_by_company_shadow",
            {"company_name": "Comercio", "headcount": 1},
        ),
        ("sfHeadcountByCompany", {"company_name": "Comercio", "headcount": 1}),
        ("sf-headcount-by-company", {"company_name": "Comercio", "headcount": 1}),
        ("headcount-by-company", {"company_name": "Comercio", "headcount": 1}),
        ("sf.headcount.by.company", {"company_name": "Comercio", "headcount": 1}),
        ("sf-active-headcount", {"company_name": "Comercio", "headcount": 1}),
        ("sf_headcount_", {"company_name": "Comercio", "headcount": 1}),
        ("sf_head__count", {"company_name": "Comercio", "headcount": 1}),
        ("sf_api_key", {"company_name": "Comercio", "headcount": 1}),
        ("sf_job_id", {"company_name": "Comercio", "headcount": 1}),
        ("sf_dataset_private", {"company_name": "Comercio", "headcount": 1}),
        (
            "evil_sf_active_headcount_shadow",
            {"company_name": "Comercio", "headcount": 1},
        ),
        ("active-headcount", {"company_name": "Comercio", "headcount": 1}),
    ),
)
def test_noncanonical_widget_identity_fails_closed(
    widget_id: object,
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
    assert widget.get("id") is None
    assert (widget["status"], widget["value"], widget["rows"]) == (
        "invalid_schema",
        None,
        [],
    )


@pytest.mark.parametrize(
    "missing_label",
    (
        "1000",
        "0001",
        "null",
        "NULL",
        "None",
        "undefined",
        "missing",
        "N/A",
        "n.a.",
        "Unknown",
        "Sin nombre",
        "(unknown)",
        "<null>",
        "[missing]",
        "NA",
        "not available",
        "sin datos",
        "desconocido",
        "blocked",
        "invalid_schema",
        "1.0",
        "1e3",
        "0b101",
        "NaN",
        "Infinity",
        "1_000",
        "(1000)",
        "<1000>",
        "1 000",
        "1'000",
        "+ 1",
        "+1",
        "-",
        "Unknown.",
        "N/A.",
        '"unknown"',
        "'unknown'",
        '"null"',
        "not applicable",
        "no data",
        "not set",
        "not provided",
        "nil",
        "void",
        "desconocida",
        "sin información",
        "ready",
        "ok",
        "partial",
        "1,000,000",
        "1.000.000",
        "1’000",
        "1.2.3",
        "1/2",
        "½",
        "1.234.567",
        "1,234,567",
        "1.000,00",
        "1,000.00",
    ),
)
def test_missing_or_machine_only_dimension_name_never_becomes_ready(
    missing_label: str,
) -> None:
    response = _get(
        {
            "widgets": [
                {
                    "id": "sf_headcount_by_company",
                    "value": 1,
                    "status": "ready",
                    "rows": [{"company_name": missing_label, "headcount": 1}],
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
    "business_name", ("3M", "7-Eleven", "Área 51", "123 Solutions")
)
def test_alphanumeric_business_name_remains_ready(business_name: str) -> None:
    response = _get(
        {
            "widgets": [
                {
                    "id": "sf_headcount_by_company",
                    "value": 1,
                    "status": "ready",
                    "rows": [{"company_name": business_name, "headcount": 1}],
                }
            ]
        }
    )

    assert response.status_code == 200
    widget = response.json()["widgets"][0]
    assert (widget["status"], widget["value"]) == ("ready", 1)
    assert widget["rows"][0]["company_name"] == business_name


@pytest.mark.parametrize("title", ("blocked", "invalid_schema", "(unknown)", "1.0"))
def test_missing_or_diagnostic_title_invalidates_widget(title: str) -> None:
    response = _get(
        {
            "widgets": [
                {
                    "id": "sf_headcount_by_company",
                    "title": title,
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


def test_generic_headcount_synthesizes_and_validates_public_label() -> None:
    response = _get(
        {
            "widgets": [
                {
                    "id": "sf_headcount",
                    "value": 1,
                    "status": "ready",
                    "rows": [{"company_name": "Comercio", "headcount": 1}],
                }
            ]
        }
    )

    assert response.status_code == 200
    widget = response.json()["widgets"][0]
    assert (widget["status"], widget["value"]) == ("ready", 1)
    assert len(widget["rows"]) == 1
    assert {
        key: widget["rows"][0][key] for key in ("label", "company_name", "headcount")
    } == {"label": "Comercio", "company_name": "Comercio", "headcount": 1}
