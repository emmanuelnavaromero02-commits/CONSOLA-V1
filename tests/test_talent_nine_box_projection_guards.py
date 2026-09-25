from __future__ import annotations

import duckdb
import pytest
from decimal import Decimal

from app.services.control_room import api as control_room_api
from tests.test_talent_nine_box_downstream import MACRO, _copy, _dataset_sql


def test_downstream_projections_count_only_valid_score_rows(tmp_path):
    con = duckdb.connect()
    try:
        con.execute(MACRO.read_text(encoding="utf-8"))
        con.execute(
            """CREATE TABLE nine_box_src AS
            SELECT * FROM (VALUES
              ('negative', -1.0::DOUBLE, 80.0::DOUBLE),
              ('nan', 'NaN'::DOUBLE, 80.0::DOUBLE),
              ('infinity', 'Infinity'::DOUBLE, 80.0::DOUBLE),
              ('huge', 1000.0::DOUBLE, 80.0::DOUBLE),
              ('valid', 100.0::DOUBLE, 100.0::DOUBLE)
            ) v(user_id, performance_score, potential_score)"""
        )
        con.execute(
            """ALTER TABLE nine_box_src ADD COLUMN box_key VARCHAR DEFAULT 'estrella';
            ALTER TABLE nine_box_src ADD COLUMN box_label VARCHAR DEFAULT 'Estrella';
            ALTER TABLE nine_box_src ADD COLUMN box_status VARCHAR DEFAULT 'ready';
            ALTER TABLE nine_box_src ADD COLUMN source_mode VARCHAR DEFAULT 'cpa_real';
            ALTER TABLE nine_box_src ADD COLUMN invalid_score_input BOOLEAN DEFAULT FALSE"""
        )
        nine_box = tmp_path / "nine-box.parquet"
        _copy(con, "nine_box_src", nine_box)

        con.execute(
            "CREATE TABLE nine_operational AS "
            + _dataset_sql(
                "sap_successfactors_talent_9box_operational",
                {"sap_successfactors_talent_9box": nine_box},
            )
        )
        assert con.execute(
            "SELECT employee_count, ready_count, blocked_count, box_status "
            "FROM nine_operational WHERE box_key='estrella'"
        ).fetchone() == (5, 1, 4, "ready")
        nine_operational = tmp_path / "nine-operational.parquet"
        _copy(con, "nine_operational", nine_operational)

        con.execute(
            """CREATE TABLE mobility AS SELECT user_id,
            'PROMOTION'::VARCHAR latest_event_reason, 0::BIGINT movement_events
            FROM nine_box_src"""
        )
        mobility = tmp_path / "mobility.parquet"
        _copy(con, "mobility", mobility)
        con.execute(
            "CREATE TABLE promotion AS "
            + _dataset_sql(
                "sap_successfactors_talent_promotion_alignment",
                {
                    "sap_successfactors_talent_9box": nine_box,
                    "sap_successfactors_talent_mobility_history": mobility,
                },
            )
        )
        assert con.execute(
            "SELECT promotion_count, aligned_count, misaligned_count, status "
            "FROM promotion WHERE box_key='summary'"
        ).fetchone() == (1, 1, 0, "ready")
        promotion = tmp_path / "promotion.parquet"
        _copy(con, "promotion", promotion)

        con.execute(
            "CREATE TABLE sensitivity AS "
            + _dataset_sql(
                "sap_successfactors_talent_calibration_sensitivity",
                {"sap_successfactors_talent_9box": nine_box},
            )
        )
        assert con.execute(
            "SELECT employee_count, classified_count, near_cut_count "
            "FROM sensitivity"
        ).fetchone() == (5, 1, 0)
        sensitivity = tmp_path / "sensitivity.parquet"
        _copy(con, "sensitivity", sensitivity)

        con.execute(
            """CREATE TABLE readiness AS SELECT user_id,
            CASE WHEN user_id='valid' THEN 'ready' ELSE 'insufficient_data' END
              AS readiness_status,
            CASE WHEN user_id='valid' THEN FALSE
                 WHEN user_id='negative' THEN NULL
                 ELSE TRUE END AS invalid_score_input
            FROM nine_box_src"""
        )
        readiness = tmp_path / "readiness.parquet"
        _copy(con, "readiness", readiness)
        empty_specs = {
            "risk": (
                "risk_band VARCHAR, status VARCHAR, "
                "invalid_score_input BOOLEAN, fit_score DOUBLE"
            ),
            "role_fit": (
                "assignment_recommendation VARCHAR, status VARCHAR, "
                "invalid_score_input BOOLEAN, fit_score DOUBLE"
            ),
            "roles": "required_skills_status VARCHAR",
        }
        paths = {}
        for name, columns in empty_specs.items():
            con.execute(f"CREATE TABLE {name} ({columns})")
            paths[name] = tmp_path / f"{name}.parquet"
            _copy(con, name, paths[name])

        con.execute(
            "CREATE TABLE candidates AS "
            + _dataset_sql(
                "sap_successfactors_talent_action_candidates",
                {
                    "sap_successfactors_talent_readiness": readiness,
                    "sap_successfactors_talent_9box": nine_box,
                    "sap_successfactors_talent_mobility_history": mobility,
                    "sap_successfactors_talent_retention_risk": paths["risk"],
                    "sap_successfactors_talent_promotion_alignment": promotion,
                    "sap_successfactors_talent_calibration_sensitivity": sensitivity,
                    "sap_successfactors_talent_role_fit_assignments": paths["role_fit"],
                },
            )
        )
        assert con.execute(
            "SELECT action_id, affected_count FROM candidates ORDER BY action_id"
        ).fetchall() == [("talent_9box_operational_ready", 1)]
        candidates = tmp_path / "candidates.parquet"
        _copy(con, "candidates", candidates)

        con.execute(
            "CREATE TABLE signals AS "
            + _dataset_sql(
                "sap_successfactors_talent_signals",
                {
                    "sap_successfactors_talent_action_candidates": candidates,
                    "sap_successfactors_talent_role_profile": paths["roles"],
                    "sap_successfactors_talent_mobility_history": mobility,
                },
            )
        )
        assert con.execute(
            "SELECT signal_id, affected_count, readiness_status FROM signals"
        ).fetchall() == [("talent_9box_operational_ready", 1, "gold_ready")]
    finally:
        con.close()


def test_unknown_status_never_counts_as_available():
    for status in ("banana", "partial", "unavailable", ""):
        row = {
            "box_status": status,
            "performance_score": 80.0,
            "potential_score": 80.0,
        }
        assert control_room_api._sf_talent_nine_box_available_count({}, [row]) == 0


def test_invalid_readiness_detail_overrules_stale_calculable_aggregate():
    aggregate = {"calculable_count": 99, "readiness_pending_count": 0}
    invalid = [
        {
            "source_mode": "cpa_real",
            "readiness_status": "ready",
            "invalid_score_input": True,
            "competency_score": 80.0,
            "performance_score": -1.0,
            "aspiration_score": 80.0,
        }
    ]
    counts = control_room_api._sf_talent_readiness_counts(aggregate, invalid)
    assert counts == {"readiness_calculable": 0, "readiness_insufficient": 1}


def test_invalid_input_provenance_overrules_in_range_derived_scores():
    row = {
        "invalid_score_input": True,
        "competency_score": 80.0,
        "performance_score": 80.0,
        "aspiration_score": 80.0,
        "potential_score": 80.0,
    }
    assert control_room_api._sf_talent_nine_box_scores_valid(row) is False
    assert control_room_api._sf_talent_cpa_scores_valid(row) is False


@pytest.mark.parametrize(
    "value",
    ["80", "-1e-400", "100.00000000000000000000000000000000001"],
)
def test_string_typed_scores_never_cross_the_public_projection(value):
    assert control_room_api._sf_talent_score(value) is None


@pytest.mark.parametrize(
    "value",
    [Decimal("-1e-400"), Decimal("100.000000000000000000000001")],
)
def test_decimal_boundaries_are_checked_before_public_float_conversion(value):
    assert control_room_api._sf_talent_score(value) is None


def test_missing_or_null_validation_provenance_fails_closed():
    base = {
        "competency_score": 80.0,
        "performance_score": 80.0,
        "aspiration_score": 80.0,
        "potential_score": 80.0,
    }
    assert control_room_api._sf_talent_cpa_scores_valid(base) is False
    assert control_room_api._sf_talent_nine_box_scores_valid(base) is False
    assert (
        control_room_api._sf_talent_cpa_scores_valid(
            {**base, "invalid_score_input": None}
        )
        is False
    )
    for provenance in (
        {},
        {"invalid_score_input": None},
        {"invalid_score_input": True},
    ):
        row = {**base, **provenance, "fit_score": 80.0, "potential_pending": True}
        masked = control_room_api._sf_talent_masked_roster_row(row)
        assert masked["performance_band_available"] == "insufficient_data"
        assert masked["fit_band"] == "insufficient_data"
        assert masked["desempeno_disponible"] is False
        assert (
            control_room_api._sf_talent_cpa_readiness_counts([row])[
                "performance_present"
            ]
            == 0
        )


def test_stale_score_signal_requires_current_server_validation_marker():
    stale = {
        "signal_id": "talent_promotion_alignment",
        "affected_count": 1,
        "recommendation": "old invalid recommendation",
    }
    valid = {**stale, "source_validation_status": "server_validated_v1"}
    unrelated = {"signal_id": "talent_mobility_observed", "affected_count": 1}

    assert control_room_api._sf_talent_validated_signal_rows([stale, unrelated], 1) == [
        unrelated
    ]
    assert control_room_api._sf_talent_validated_signal_rows([valid, unrelated], 1) == [
        valid,
        unrelated,
    ]
    assert control_room_api._sf_talent_validated_signal_rows([valid], 0) == []


def test_missing_input_signal_requires_marker_but_not_a_calculable_row():
    stale = {
        "signal_id": "talent_cpa_missing_inputs",
        "affected_count": 1,
    }
    valid = {**stale, "source_validation_status": "server_validated_v1"}

    assert control_room_api._sf_talent_validated_signal_rows([stale], 0) == []
    assert control_room_api._sf_talent_validated_signal_rows([valid], 0) == [valid]
