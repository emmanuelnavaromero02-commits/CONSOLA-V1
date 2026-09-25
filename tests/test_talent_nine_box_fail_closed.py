from __future__ import annotations

import math
from pathlib import Path

import duckdb
import pytest

from app.services.control_room import api as control_room_api


ROOT = Path(__file__).resolve().parents[1]
DATASETS = ROOT / "cartridges/sap_successfactors/datasets"
SHARED_SQL = ROOT / "refinement/app/sql"
NINE_BOX = DATASETS / "sap_successfactors_talent_9box.sql"

INVALID_CASES = [
    pytest.param(-1.0, id="negative"),
    pytest.param(float("nan"), id="nan"),
    pytest.param(float("inf"), id="infinity"),
    pytest.param(1000.0, id="huge"),
]

VALID_CASES = [
    pytest.param(0.0, 0.0, id="zero"),
    pytest.param(3.0, 3.0, id="mid-scale"),
    pytest.param(5.0, 5.0, id="upper-scale-boundary"),
    pytest.param(100.0, 5.0, id="upper-percent-boundary"),
    pytest.param(20.0, 1.0, id="percent-rescaled"),
]


def _normalization_sql() -> str:
    body = NINE_BOX.read_text(encoding="utf-8")
    assert (
        "talent_percent_scale" in body
    ), "9-box must route every score through the shared normalization"
    return SHARED_SQL / "talent_score_scale.sql"


def _scale(con: duckdb.DuckDBPyConnection, value: float) -> float | None:
    macro = (SHARED_SQL / "talent_score_scale.sql").read_text(encoding="utf-8")
    con.execute(macro)
    row = con.execute("SELECT talent_score_scale(?::DOUBLE)", [value]).fetchone()
    return row[0]


@pytest.fixture()
def con():
    connection = duckdb.connect()
    try:
        yield connection
    finally:
        connection.close()


@pytest.mark.parametrize("value", INVALID_CASES)
def test_invalid_scores_normalize_to_null(con, value):
    assert _scale(con, value) is None


@pytest.mark.parametrize("value,expected", VALID_CASES)
def test_valid_scores_and_boundaries_are_preserved(con, value, expected):
    result = _scale(con, value)
    assert result is not None
    assert math.isclose(result, expected, rel_tol=1e-9)


def test_null_stays_null(con):
    macro = (SHARED_SQL / "talent_score_scale.sql").read_text(encoding="utf-8")
    con.execute(macro)
    assert con.execute("SELECT talent_score_scale(NULL::DOUBLE)").fetchone()[0] is None


@pytest.mark.parametrize("value", [pytest.param(100.001, id="just-over-percent")])
def test_values_just_outside_the_domain_are_rejected(con, value):
    assert _scale(con, value) is None


@pytest.mark.parametrize(
    "value,expected",
    [(0.0, 0.0), (5.0, 0.25), (100.0, 5.0)],
)
def test_percent_scores_are_never_reinterpreted_as_five_point_ratings(
    con, value, expected
):
    con.execute((SHARED_SQL / "talent_score_scale.sql").read_text(encoding="utf-8"))
    result = con.execute("SELECT talent_percent_scale(?::DOUBLE)", [value]).fetchone()[
        0
    ]
    assert result == expected


def test_nine_box_sql_validates_finiteness_before_banding():
    body = NINE_BOX.read_text(encoding="utf-8")

    assert "talent_percent_scale(" in body
    scored = body.split("scored AS (", 1)[1].split("banded AS (", 1)[0]
    assert "TRY_CAST(performance_score AS DOUBLE) > 5" not in scored
    assert "TRY_CAST(competency_score AS DOUBLE) > 5" not in scored


SCORING_DATASETS = [
    "sap_successfactors_talent_employee_profile.sql",
    "sap_successfactors_talent_cpa_scores.sql",
    "sap_successfactors_talent_9box.sql",
]
CONSUMING_DATASETS = [
    "sap_successfactors_talent_action_candidates.sql",
    "sap_successfactors_talent_signals.sql",
]


@pytest.mark.parametrize("dataset", SCORING_DATASETS)
def test_scoring_datasets_apply_the_shared_normalization(dataset):
    body = (DATASETS / dataset).read_text(encoding="utf-8")
    assert "talent_" in body and ("_scale(" in body or "_is_valid(" in body)


@pytest.mark.parametrize("dataset", CONSUMING_DATASETS)
def test_consuming_datasets_never_recast_raw_scores(dataset):
    body = (DATASETS / dataset).read_text(encoding="utf-8")
    assert "TRY_CAST(performance_score" not in body
    assert "TRY_CAST(competency_score" not in body
    assert "TRY_CAST(aspiration_score" not in body


def test_invalid_scores_never_reach_control_room_serialization():
    api = (ROOT / "console/app/services/control_room/api.py").read_text(
        encoding="utf-8"
    )
    assert "_finite_number" in api or "isfinite" in api


@pytest.mark.parametrize("value", INVALID_CASES)
def test_control_room_never_treats_invalid_scores_as_available(value):
    row = {
        "user_id": "employee-1",
        "box_key": "estrella",
        "box_label": "Estrella",
        "box_status": "ready",
        "source_mode": "cpa_real",
        "cpa_status": "ready",
        "performance_score": value,
        "potential_score": 80.0,
        "performance_band": "high",
        "performance_band_available": "high",
        "potential_band": "high",
    }

    assert control_room_api._sf_talent_score(value) is None
    if not math.isfinite(value):
        assert control_room_api._sf_talent_float(value) is None
    assert control_room_api._sf_talent_performance_band(value) is None
    assert control_room_api._sf_talent_nine_box_available_count({}, [row]) == 0
    aggregate = control_room_api._sf_talent_9box_operational_rows_from_detail([row])
    assert aggregate[0]["ready_count"] == 0
    assert aggregate[0]["blocked_count"] == 1
    assert aggregate[0]["box_status"] == "blocked"
    masked = control_room_api._sf_talent_masked_roster_row(row)
    assert masked["data_status"] == "blocked"
    assert masked["box_id"] == ""


def test_refinement_image_contains_the_canonical_score_macro():
    packaged = ROOT / "refinement/app/sql/talent_score_scale.sql"
    engine = (ROOT / "refinement/app/duckdb_engine.py").read_text(encoding="utf-8")

    assert packaged.is_file()
    assert "app/sql/talent_score_scale.sql" in engine or (
        "Path(__file__).resolve().parent" in engine
        and "talent_score_scale.sql" in engine
    )


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_public_projection_never_emits_non_finite_numbers(value):
    assert control_room_api._sf_talent_public_value(value) is None


def test_promotion_and_action_sql_require_a_valid_ready_9box_row():
    promotion = (
        (DATASETS / "sap_successfactors_talent_promotion_alignment.sql")
        .read_text(encoding="utf-8")
        .lower()
    )
    candidates = (
        (DATASETS / "sap_successfactors_talent_action_candidates.sql")
        .read_text(encoding="utf-8")
        .lower()
    )

    assert "nine_box.box_status = 'ready'" in promotion
    assert "from nine_box_detail as nb, mobility as mv" in candidates
    assert "not coalesce(nb.invalid_score_input, true)" in candidates
    assert "talent_percent_is_valid(nb.performance_score)" in candidates


def test_null_raw_score_cannot_be_laundered_by_a_proxy():
    row = {
        "performance_score": None,
        "performance_proxy_score": 80.0,
        "potential_score": 80.0,
        "box_status": "ready",
    }
    assert control_room_api._sf_talent_nine_box_scores_valid(row) is False


def test_operational_count_never_overrules_invalid_detail():
    operational = {"nine_box_classified_count": 99}
    invalid = {
        "box_key": "estrella",
        "box_status": "ready",
        "performance_score": -1.0,
        "potential_score": 80.0,
    }
    assert (
        control_room_api._sf_talent_nine_box_available_count(operational, [invalid])
        == 0
    )


def test_valid_performance_remains_available_while_potential_is_pending():
    row = {
        "user_id": "employee-1",
        "performance_score": 80.0,
        "potential_score": None,
        "performance_band_available": "high",
        "potential_pending": True,
        "box_status": "blocked",
        "invalid_score_input": False,
    }
    masked = control_room_api._sf_talent_masked_roster_row(row)
    assert masked["performance_band_available"] == "high"
    assert masked["desempeno_disponible"] is True
    assert masked["data_status"] == "blocked"


def test_invalid_benchmark_row_cannot_steal_valid_cpa_attribution():
    rows = [
        {
            "box_key": "core",
            "box_status": "ready",
            "source_mode": "cpa_real",
            "performance_score": 80.0,
            "potential_score": 80.0,
            "invalid_score_input": False,
        },
        {
            "box_key": "core",
            "box_status": "ready",
            "source_mode": "benchmark_internal",
            "performance_score": -1.0,
            "potential_score": 80.0,
        },
    ]
    aggregate = control_room_api._sf_talent_9box_operational_rows_from_detail(rows)[0]
    assert aggregate["ready_count"] == 1
    assert aggregate["benchmark_count"] == 0
    assert aggregate["blocked_count"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("value", INVALID_CASES)
async def test_public_endpoint_rebuilds_stale_ready_aggregate_from_detail(
    monkeypatch, value
):
    async def result(dataset, _user, _limit):
        if dataset.endswith("9box_operational"):
            rows = [
                {
                    "box_key": "estrella",
                    "employee_count": 1,
                    "ready_count": 1,
                    "blocked_count": 0,
                    "benchmark_count": 0,
                    "box_status": "ready",
                }
            ]
        else:
            rows = [
                {
                    "user_id": "employee-1",
                    "box_key": "estrella",
                    "box_status": "ready",
                    "source_mode": "cpa_real",
                    "performance_score": value,
                    "potential_score": 80.0,
                }
            ]
        return {"dataset": dataset, "rows": rows, "status": "ready", "error": None}

    monkeypatch.setattr(control_room_api._core, "_sf_talent_gold_result", result)
    payload = await control_room_api.sap_successfactors_talent_9box(
        {"tenant_id": "tenant-a", "workspace_id": "workspace-a"}
    )

    assert payload["status"] == "blocked"
    assert payload["totals"]["ready"] == 0
    assert (
        next(cell for cell in payload["cells"] if cell["box_id"] == "estrella")[
            "status"
        ]
        == "blocked"
    )
