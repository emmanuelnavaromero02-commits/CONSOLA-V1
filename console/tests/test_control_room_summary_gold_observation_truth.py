from __future__ import annotations

from pathlib import Path

import pytest

from app.schemas.control_room_legacy_responses import (
    ControlRoomBusinessSummaryResponse,
    ControlRoomGoldKpisResponse,
)
from app.services import control_room_service


USER = {
    "id": "user-1",
    "tenant_id": "tenant-1",
    "active_tenant_id": "tenant-1",
    "active_workspace_id": "workspace-1",
}
REPO_ROOT = Path(__file__).resolve().parents[2]


def _summary_sources(*sources: object) -> list[dict[str, object]]:
    return ControlRoomBusinessSummaryResponse.project(
        {"sources": list(sources)}
    ).model_dump()["sources"]


@pytest.mark.parametrize(
    "state",
    [
        "missing",
        "blocked",
        "unavailable",
        "error",
        "partial",
        "stub",
        "no_permission",
        "schema_only",
        "invalid_schema",
    ],
)
def test_summary_omits_non_observed_source_states(state: str):
    assert (
        _summary_sources(
            {
                "cartridge": "sap_hcm",
                "count": 0,
                "status": state,
                "checked_at": "2026-07-25T10:00:00Z",
            }
        )
        == []
    )


@pytest.mark.parametrize("count", [True, False, -1, 1.0, "1", None])
def test_summary_requires_an_explicit_nonnegative_integer(count: object):
    assert (
        _summary_sources({"cartridge": "sap_hcm", "count": count, "status": "ok"}) == []
    )


@pytest.mark.parametrize("state", ["loading", "not_ready", "pending", "unknown"])
def test_summary_omits_unrecognized_non_success_states(state: str):
    assert (
        _summary_sources(
            {
                "cartridge": "sap_hcm",
                "count": 3,
                "status": state,
                "evaluation_status": "success",
            }
        )
        == []
    )


def test_summary_preserves_only_observed_zero_and_valid_positive():
    assert _summary_sources(
        {"cartridge": "sap_successfactors", "count": 9},
        {
            "cartridge": "sap_hcm",
            "count": 0,
            "status": "empty",
            "checked_at": "2026-07-25T10:00:00Z",
        },
        {"cartridge": "replicon", "count": 0, "status": "empty"},
        {
            "cartridge": "sap_s4hana",
            "count": 4,
            "status": "empty",
            "checked_at": "2026-07-25T10:00:00Z",
        },
        {
            "cartridge": "sap_s4hana",
            "count": 0,
            "status": "ok",
            "generated_at": "2026-07-25T10:00:00Z",
        },
        {
            "cartridge": "replicon",
            "count": 0,
            "status": "ready",
            "observed_at": {"poison": "not-a-date"},
        },
    ) == [
        {"label": "Personal y nomina", "count": 0},
        {"label": "Talento y organizacion", "count": 9},
    ]


def test_summary_stale_requires_valid_date_and_nested_evidence():
    projected = ControlRoomBusinessSummaryResponse.project(
        {
            "sources": [
                {
                    "cartridge": "sap_hcm",
                    "count": 3,
                    "status": "stale",
                    "evaluation_status": "success",
                    "observed_at": "2026-07-25T10:00:00Z",
                    "dataset": "sf_headcount",
                    "evidence": {"references": ["sf_headcount:observed-row-1"]},
                },
                {
                    "cartridge": "sap_s4hana",
                    "count": 4,
                    "status": "stale",
                    "observed_at": "2026-07-25T10:00:00Z",
                },
            ]
        }
    ).model_dump()

    assert projected["sources"] == [{"label": "Personal y nomina", "count": 3}]
    serialized = str(projected)
    assert "stale" not in serialized
    assert "sf_headcount" not in serialized
    assert "observed-row-1" not in serialized


def test_gold_rows_reject_absence_and_invalid_counts_but_keep_real_zero():
    rows = control_room_service._sf_gold_top_headcount_rows(
        [
            {"company_name": "Cero real", "headcount": 0},
            {"company_name": "Positivo", "headcount": 7},
            {"company_name": None, "company_id": "private", "headcount": 8},
            {"company_name": "   ", "headcount": 8},
            {"company_name": " (SIN NOMBRE) ", "headcount": 8},
            {"company_name": "Booleano", "headcount": True},
            {"company_name": "Negativo", "headcount": -1},
            {"company_name": "Decimal", "headcount": 1.5},
            {"company_name": "Integral decimal", "headcount": 2.0},
            {"company_name": "Texto", "headcount": "3"},
            {"company_name": "Ausente"},
        ],
        ("company_id", "company_name"),
    )

    assert rows == [
        {"label": "Positivo", "company_name": "Positivo", "headcount": 7},
        {"label": "Cero real", "company_name": "Cero real", "headcount": 0},
    ]
    assert control_room_service._sf_gold_headcount_total(rows) == 7
    assert control_room_service._sf_gold_headcount_total([{"headcount": 0}]) == 0
    assert control_room_service._sf_gold_headcount_total([]) is None


@pytest.mark.parametrize("status", ["empty", "missing", "blocked"])
def test_gold_projection_never_turns_absent_dataset_into_zero(status: str):
    widget = ControlRoomGoldKpisResponse.project(
        {
            "widgets": [
                {
                    "id": "sf_headcount_by_company",
                    "value": 0,
                    "status": status,
                    "rows": [{"company_name": "Comercio", "headcount": 0}],
                }
            ]
        }
    ).model_dump()["widgets"][0]

    assert widget["value"] is None
    assert widget["rows"] == []
    assert widget["status"] == status


def test_gold_projection_keeps_named_observed_zero():
    widget = ControlRoomGoldKpisResponse.project(
        {
            "widgets": [
                {
                    "id": "sf_headcount_by_company",
                    "value": 0,
                    "status": "ready",
                    "rows": [{"company_name": "Comercio", "headcount": 0}],
                }
            ]
        }
    ).model_dump()["widgets"][0]

    assert widget["value"] == 0
    assert len(widget["rows"]) == 1
    assert widget["rows"][0]["label"] == "Comercio"
    assert widget["rows"][0]["company_name"] == "Comercio"
    assert widget["rows"][0]["headcount"] == 0
    assert widget["status"] == "ready"


@pytest.mark.parametrize("value", [None, True, 1.5, "3"])
def test_gold_projection_does_not_rebuild_total_from_visible_rows(value: object):
    widget = ControlRoomGoldKpisResponse.project(
        {
            "widgets": [
                {
                    "id": "sf_headcount_by_company",
                    "value": value,
                    "status": "ready",
                    "rows": [{"company_name": "Comercio", "headcount": 3}],
                }
            ]
        }
    ).model_dump()["widgets"][0]

    assert widget["value"] is None
    assert widget["rows"] == []
    assert widget["status"] == "invalid_schema"


def test_successfactors_gold_sql_does_not_fabricate_business_names():
    for dimension in ("company", "location", "department"):
        sql = (
            REPO_ROOT
            / "cartridges"
            / "sap_successfactors"
            / "datasets"
            / f"sap_successfactors_headcount_by_{dimension}.sql"
        ).read_text(encoding="utf-8")
        lowered = sql.casefold()
        assert "coalesce" not in lowered
        assert "(sin nombre)" in lowered
        assert f"trim({dimension}_name)" in lowered
        assert f"order by headcount desc, {dimension}_name" in lowered
