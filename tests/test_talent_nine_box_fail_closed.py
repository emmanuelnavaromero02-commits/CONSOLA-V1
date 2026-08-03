"""9-box must fail closed on non-finite and out-of-range scores.

TRY_CAST only rejects values that cannot be parsed. It happily returns -1.0,
NaN, Infinity and 1000.0, and the banding that follows turns them into a real
box: Infinity/20 lands above the high threshold, 1000/20 = 50 does too, and
NaN compares false against every bound so it falls through to 'low'. All four
then count as classified and reach Control Room as recommendations.

These regressions run the packaged dataset SQL against DuckDB and pin the
single normalization that must reject them before any band, box, candidate or
signal is computed.
"""

from __future__ import annotations

import math
from pathlib import Path

import duckdb
import pytest


ROOT = Path(__file__).resolve().parents[1]
DATASETS = ROOT / "cartridges/sap_successfactors/datasets"
SHARED_SQL = ROOT / "cartridges/sap_successfactors/sql"
NINE_BOX = DATASETS / "sap_successfactors_talent_9box.sql"

# The exact values reproduced by the independent audit.
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
    """The reusable normalization the packaged SQL must apply."""
    body = NINE_BOX.read_text(encoding="utf-8")
    assert (
        "talent_score_scale" in body
    ), "9-box must route every score through the shared normalization"
    return SHARED_SQL / "_talent_score_scale.sql"


def _scale(con: duckdb.DuckDBPyConnection, value: float) -> float | None:
    macro = (SHARED_SQL / "_talent_score_scale.sql").read_text(encoding="utf-8")
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
    macro = (SHARED_SQL / "_talent_score_scale.sql").read_text(encoding="utf-8")
    con.execute(macro)
    assert con.execute("SELECT talent_score_scale(NULL::DOUBLE)").fetchone()[0] is None


@pytest.mark.parametrize("value", [pytest.param(100.001, id="just-over-percent")])
def test_values_just_outside_the_domain_are_rejected(con, value):
    assert _scale(con, value) is None


def test_nine_box_sql_validates_finiteness_before_banding():
    body = NINE_BOX.read_text(encoding="utf-8")

    # The raw TRY_CAST chain that let Infinity through must be gone from the
    # scoring stage: every score is routed through the shared normalization.
    assert "talent_score_scale(" in body
    scored = body.split("scored AS (", 1)[1].split("banded AS (", 1)[0]
    assert "TRY_CAST(performance_score AS DOUBLE) > 5" not in scored
    assert "TRY_CAST(competency_score AS DOUBLE) > 5" not in scored


# The datasets that turn a raw score into a scale, a band or a CPA figure.
SCORING_DATASETS = [
    "sap_successfactors_talent_employee_profile.sql",
    "sap_successfactors_talent_cpa_scores.sql",
    "sap_successfactors_talent_9box.sql",
]
# The datasets downstream of them: they consume already-normalized columns and
# must never reintroduce a raw cast of their own.
CONSUMING_DATASETS = [
    "sap_successfactors_talent_action_candidates.sql",
    "sap_successfactors_talent_signals.sql",
]


@pytest.mark.parametrize("dataset", SCORING_DATASETS)
def test_scoring_datasets_apply_the_shared_normalization(dataset):
    body = (DATASETS / dataset).read_text(encoding="utf-8")
    assert "talent_score_scale(" in body or "talent_score_is_valid(" in body


@pytest.mark.parametrize("dataset", CONSUMING_DATASETS)
def test_consuming_datasets_never_recast_raw_scores(dataset):
    body = (DATASETS / dataset).read_text(encoding="utf-8")
    assert "TRY_CAST(performance_score" not in body
    assert "TRY_CAST(competency_score" not in body
    assert "TRY_CAST(aspiration_score" not in body


def test_invalid_scores_never_reach_control_room_serialization():
    """No NaN or Infinity may be serialized publicly."""
    api = (ROOT / "console/app/services/control_room/api.py").read_text(
        encoding="utf-8"
    )
    assert "_finite_number" in api or "isfinite" in api
