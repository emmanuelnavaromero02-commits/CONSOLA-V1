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


P2_BUSINESS_LABELS = (
    "Sales Receipts",
    "Gross Receipts",
    "State: California",
    "State: Texas",
    "Status: Won",
    "Data Status: Green",
    "Binding Agreements",
    "Supply Chain Provenance",
    "State pension review",
    "Status meeting today",
    "Receipt of annual leave request",
    "Binding employment agreement",
    "Provenance of organic coffee",
)
CLASS_1_SQL_LABELS = (
    "TABLE Rock",
    "SELECT Comercial",
    "TRUNCATE Labs",
    "Show Solutions",
    "Describe Digital",
)
CLASS_2_BUSINESS_LABELS = (
    "Show me the Q4 report",
    "Call center roster",
    "Set of core values",
    "Grant Portfolio Review",
    "Copy of the signed contract",
    "Describe the onboarding process",
    "Use of force policy",
)
STRUCTURED_TECHNICAL_LABELS = (
    "status: ready",
    "source_status",
    "dataStatus",
    "receipt=rcpt-1",
    "receipt_id",
    "receiptId",
    "actionBindingsMap",
    "provenance_record",
    "Update status set to ready",
)


def _payload(*, rows: list[object], value: object, status: object = "ready") -> dict:
    return {
        "tenant_id": "must-not-leak",
        "workspace_id": "must-not-leak",
        "widgets": [
            {
                "id": "sf_headcount_by_company",
                "title": "SELECT secret FROM private_titles",
                "value": value,
                "status": status,
                "_coverage_observed": True,
                "rows": rows,
            }
        ],
    }


def _direct(payload: dict) -> dict:
    return ControlRoomGoldKpisResponse.project(payload).model_dump()["widgets"][0]


def _factory(payload: dict) -> dict:
    return project_public_gold_response(payload)["widgets"][0]


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
    assert "must-not-leak" not in response.text
    assert "private_titles" not in response.text
    return response.json()["widgets"][0]


PROJECTORS = (_factory, _direct, _http)


def _headcount_rows(widget: dict) -> list[dict[str, object]]:
    return [
        {
            "label": row["label"],
            "company_name": row["company_name"],
            "headcount": row["headcount"],
        }
        for row in widget["rows"]
    ]


@pytest.mark.parametrize("project", PROJECTORS)
def test_gold_terminal_factory_preserves_label_bytes_and_uses_server_title(
    project,
) -> None:
    label = "  Rotacio\u0301n y talento  "

    widget = project(_payload(rows=[{"company_name": label, "headcount": 0}], value=0))

    assert widget["title"] == "Headcount por compania"
    assert widget["status"] == "ready"
    assert widget["value"] == 0
    assert _headcount_rows(widget) == [
        {"label": label, "company_name": label, "headcount": 0}
    ]


@pytest.mark.parametrize("project", PROJECTORS)
def test_gold_terminal_factory_publishes_only_visible_partial_sum(project) -> None:
    widget = project(
        _payload(
            value=100,
            status="partial",
            rows=[
                {"company_name": "Comercio", "headcount": 90},
                {"company_name": "SELECT salary FROM payroll", "headcount": 4},
                {"company_name": "/srv/private/payroll.csv", "headcount": 3},
                {"company_name": "password=gold-secret", "headcount": 2},
                {"company_name": "gold_private", "headcount": 1},
            ],
        )
    )

    assert widget["status"] == "partial"
    assert widget["value"] == 90
    assert _headcount_rows(widget) == [
        {"label": "Comercio", "company_name": "Comercio", "headcount": 90}
    ]


@pytest.mark.parametrize("project", PROJECTORS)
def test_gold_terminal_factory_rejects_partial_without_visible_rows(project) -> None:
    widget = project(
        _payload(
            value=10,
            status="partial",
            rows=[{"company_name": "TABLE payroll", "headcount": 10}],
        )
    )

    assert (widget["status"], widget["value"], widget["rows"]) == (
        "invalid_schema",
        None,
        [],
    )


@pytest.mark.parametrize("project", PROJECTORS)
def test_gold_ready_total_above_visible_rows_becomes_visible_partial(project) -> None:
    widget = project(
        _payload(
            value=100,
            rows=[{"company_name": "Comercio", "headcount": 90}],
        )
    )

    assert (widget["status"], widget["value"]) == ("partial", 90)
    assert _headcount_rows(widget) == [
        {"label": "Comercio", "company_name": "Comercio", "headcount": 90}
    ]


@pytest.mark.parametrize("project", PROJECTORS)
def test_gold_value_is_sum_of_six_rows_actually_published(project) -> None:
    rows = [
        {"company_name": f"Company {headcount}", "headcount": headcount}
        for headcount in range(7, 0, -1)
    ]

    widget = project(_payload(value=28, rows=rows))

    assert widget["status"] == "partial"
    assert widget["value"] == 27
    assert len(widget["rows"]) == 6
    assert sum(row["headcount"] for row in widget["rows"]) == widget["value"]
    assert all(row["company_name"] != "Company 1" for row in widget["rows"])


@pytest.mark.parametrize("project", PROJECTORS)
def test_gold_raw_total_below_visible_sum_fails_closed(project) -> None:
    widget = project(
        _payload(
            value=89,
            rows=[{"company_name": "Comercio", "headcount": 90}],
        )
    )

    assert (widget["status"], widget["value"], widget["rows"]) == (
        "invalid_schema",
        None,
        [],
    )


@pytest.mark.parametrize("invalid", (True, 1.0, Decimal("1"), "1", -1, None))
def test_gold_terminal_factory_rejects_non_integer_headcount(invalid: object) -> None:
    widget = _direct(
        _payload(
            value=1,
            rows=[{"company_name": "Comercio", "headcount": invalid}],
        )
    )

    assert (widget["status"], widget["value"], widget["rows"]) == (
        "invalid_schema",
        None,
        [],
    )


@pytest.mark.parametrize(
    "ambiguous",
    (
        "SELECT salary FROM payroll",
        "FROM payroll",
        "TABLE payroll",
    ),
)
def test_gold_untrusted_label_omits_real_query_grammar(ambiguous: str) -> None:
    widget = _direct(
        _payload(value=1, rows=[{"company_name": ambiguous, "headcount": 1}])
    )

    assert (widget["status"], widget["value"], widget["rows"]) == (
        "invalid_schema",
        None,
        [],
    )


@pytest.mark.parametrize("project", PROJECTORS)
@pytest.mark.parametrize(
    "label",
    P2_BUSINESS_LABELS + CLASS_2_BUSINESS_LABELS,
)
def test_gold_factory_preserves_p2_and_class_2_labels_byte_exact(
    project,
    label: str,
) -> None:
    widget = project(_payload(value=1, rows=[{"company_name": label, "headcount": 1}]))

    assert (widget["status"], widget["value"]) == ("ready", 1)
    assert _headcount_rows(widget) == [
        {"label": label, "company_name": label, "headcount": 1}
    ]


@pytest.mark.parametrize("project", PROJECTORS)
@pytest.mark.parametrize(
    "label",
    CLASS_1_SQL_LABELS + STRUCTURED_TECHNICAL_LABELS,
)
def test_gold_factory_rejects_class_1_and_structured_technical_labels(
    project,
    label: str,
) -> None:
    widget = project(_payload(value=1, rows=[{"company_name": label, "headcount": 1}]))

    assert (widget["status"], widget["value"], widget["rows"]) == (
        "invalid_schema",
        None,
        [],
    )
