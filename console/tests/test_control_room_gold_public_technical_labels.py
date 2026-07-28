from __future__ import annotations

import json
import unicodedata
from unittest.mock import AsyncMock, patch

import pytest

from app.routers import control_room
from app.services import control_room_service
from control_room_public_http_harness import DATASET_READER, client
from control_room_public_technical_canaries import TECHNICAL_PUBLIC_CANARIES


DIMENSIONS = (
    ("company", "company_name", "compañía"),
    ("location", "location_name", "ubicación"),
    ("department", "department_name", "departamento"),
)
STATEMENT_SHAPED_LABELS = (
    ("department", "department_name", "Create policy for annual leave"),
    ("company", "company_name", "Analyze workforce trends"),
    ("company", "company_name", "Refresh materialized view of talent"),
    ("company", "company_name", "Values (people first)"),
    ("company", "company_name", "Copy payroll to dashboard."),
    ("company", "company_name", "Create table for annual planning."),
    ("company", "company_name", "Comment on table design"),
    ("company", "company_name", "Insert into culture"),
    ("company", "company_name", "Create index of capabilities"),
    ("company", "company_name", "Alter role responsibilities"),
    ("company", "company_name", "Delete from shortlist."),
)


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


def _serialized_without_widget_ids(response) -> str:
    body = response.json()
    for widget in body.get("widgets", []):
        widget.pop("id", None)
    return json.dumps(body, ensure_ascii=False)


@pytest.mark.parametrize("technical", TECHNICAL_PUBLIC_CANARIES)
@pytest.mark.parametrize(("dimension", "name_key", "title"), DIMENSIONS)
def test_gold_http_rejects_technical_dimension_labels_before_ready(
    technical: str,
    dimension: str,
    name_key: str,
    title: str,
) -> None:
    response = _get(
        {
            "widgets": [
                {
                    "id": f"sf_headcount_by_{dimension}",
                    "title": f"Headcount por {title}",
                    "value": 1,
                    "status": "ready",
                    "rows": [{name_key: technical, "headcount": 1}],
                }
            ]
        }
    )

    assert response.status_code == 200
    normalized = unicodedata.normalize("NFKC", technical)
    public_copy = _serialized_without_widget_ids(response)
    assert normalized not in public_copy
    assert technical not in public_copy
    widget = response.json()["widgets"][0]
    assert widget["status"] == "invalid_schema"
    assert widget["value"] is None
    assert widget["rows"] == []


@pytest.mark.parametrize("technical", TECHNICAL_PUBLIC_CANARIES)
def test_gold_http_rejects_direct_metric_labels_before_ready(technical: str) -> None:
    response = _get(
        {
            "widgets": [
                {
                    "id": "sf_contractor_risk",
                    "title": "Riesgo de contratistas",
                    "value": 1,
                    "status": "ready",
                    "rows": [{"label": technical, "count": 1}],
                }
            ]
        }
    )

    assert response.status_code == 200
    normalized = unicodedata.normalize("NFKC", technical)
    public_copy = _serialized_without_widget_ids(response)
    assert normalized not in public_copy
    assert technical not in public_copy
    widget = response.json()["widgets"][0]
    assert widget["status"] == "invalid_schema"
    assert widget["value"] is None
    assert widget["rows"] == []


@pytest.mark.parametrize("technical", TECHNICAL_PUBLIC_CANARIES)
def test_gold_http_redacts_technical_widget_titles(technical: str) -> None:
    response = _get(
        {
            "widgets": [
                {
                    "id": "sf_headcount_by_company",
                    "title": technical,
                    "value": 1,
                    "status": "ready",
                    "rows": [{"company_name": "Comercio", "headcount": 1}],
                }
            ]
        }
    )

    assert response.status_code == 200
    normalized = unicodedata.normalize("NFKC", technical)
    public_copy = _serialized_without_widget_ids(response)
    assert normalized not in public_copy
    assert technical not in public_copy
    widget = response.json()["widgets"][0]
    assert widget["title"] == "Headcount por compania"
    assert widget["status"] == "ready"
    assert widget["value"] == 1
    assert widget["rows"][0]["company_name"] == "Comercio"


@pytest.mark.parametrize(
    ("dimension", "name_key", "label", "headcount"),
    (
        ("company", "company_name", "Comercio", 0),
        ("location", "location_name", "  Dirección de México  ", 7),
        ("department", "department_name", "Rotacio\u0301n y talento", 2),
        ("company", "company_name", "I+D (México), S.A. de C.V.", 3),
        ("location", "location_name", "Operaciones / Norte", 4),
        ("company", "company_name", "Gold Coast Operations", 5),
        ("company", "company_name", "GoldCoastOperations", 6),
        ("company", "company_name", "SuccessFactors México", 7),
        ("company", "company_name", "Gold-Leaf Logistics", 9),
        ("company", "company_name", "Silver-People Consulting", 10),
        ("company", "company_name", "Gold.Private Banking", 12),
        ("company", "company_name", "Gold.Coast Operations", 13),
        ("company", "company_name", "Silver.People Consulting", 14),
        ("company", "company_name", "Estado listo para revisión.", 1),
        ("company", "company_name", "SuccessFactorsTraining", 1),
        ("company", "company_name", "SQL Team", 1),
        ("company", "company_name", "3M", 1),
        ("company", "company_name", "7-Eleven", 1),
        ("company", "company_name", "Basic Training", 1),
        (
            "company",
            "company_name",
            "Please choose the department from the company menu.",
            1,
        ),
        ("company", "company_name", "Use payroll insights", 1),
    ),
)
def test_gold_http_preserves_legitimate_business_labels(
    dimension: str,
    name_key: str,
    label: str,
    headcount: int,
) -> None:
    response = _get(
        {
            "widgets": [
                {
                    "id": f"sf_headcount_by_{dimension}",
                    "title": "Distribución empresarial",
                    "value": headcount,
                    "status": "ready",
                    "rows": [{name_key: label, "headcount": headcount}],
                }
            ]
        }
    )

    assert response.status_code == 200
    widget = response.json()["widgets"][0]
    assert widget["status"] == "ready"
    assert widget["value"] == headcount
    assert len(widget["rows"]) == 1
    assert {
        key: widget["rows"][0][key] for key in ("label", name_key, "headcount")
    } == {"label": label, name_key: label, "headcount": headcount}


@pytest.mark.parametrize(("dimension", "name_key", "label"), STATEMENT_SHAPED_LABELS)
def test_gold_http_fails_closed_for_untrusted_statement_shaped_labels(
    dimension: str,
    name_key: str,
    label: str,
) -> None:
    response = _get(
        {
            "widgets": [
                {
                    "id": f"sf_headcount_by_{dimension}",
                    "value": 1,
                    "status": "ready",
                    "rows": [{name_key: label, "headcount": 1}],
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
    assert label not in response.text


def test_gold_http_preserves_exact_100_partial_90_and_hides_invalid_ten() -> None:
    response = _get(
        {
            "widgets": [
                {
                    "id": "sf_active_headcount",
                    "title": "Headcount total activo",
                    "value": 100,
                    "status": "ready",
                    "rows": [],
                },
                {
                    "id": "sf_headcount_by_company",
                    "title": "Headcount por compañía",
                    "value": 90,
                    "status": "partial",
                    "_coverage_observed": True,
                    "rows": [
                        {"company_name": "Comercio", "headcount": 90},
                        {
                            "company_name": "gold_sap_successfactors_employee_360",
                            "headcount": 10,
                        },
                    ],
                },
            ]
        }
    )

    assert response.status_code == 200
    active, company = response.json()["widgets"]
    assert (active["value"], active["status"]) == (100, "ready")
    assert (company["value"], company["status"]) == (90, "partial")
    assert len(company["rows"]) == 1
    assert {
        key: company["rows"][0][key] for key in ("label", "company_name", "headcount")
    } == {"label": "Comercio", "company_name": "Comercio", "headcount": 90}
    assert "gold_sap_successfactors_employee_360" not in response.text
