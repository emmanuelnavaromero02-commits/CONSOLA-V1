"""Invalid Talent scores cannot re-enter through lateral Gold datasets."""

from __future__ import annotations

import duckdb

from tests.test_talent_nine_box_downstream import MACRO, _copy, _dataset_sql


def test_invalid_fit_scores_never_create_lateral_candidates_or_signals(tmp_path):
    con = duckdb.connect()
    try:
        con.execute(MACRO.read_text(encoding="utf-8"))
        con.execute(
            """CREATE TABLE readiness_src AS SELECT
            user_id, 'Empresa'::VARCHAR company_name, 'Area'::VARCHAR department_name,
            'Madrid'::VARCHAR location_name, 'JOB'::VARCHAR job_code,
            'Rol'::VARCHAR role_name, fit_score, TRUE invalid_score_input,
            'insufficient_data'::VARCHAR readiness_status
            FROM (VALUES
              ('negative', -1.0::DOUBLE),
              ('nan', 'NaN'::DOUBLE),
              ('infinity', 'Infinity'::DOUBLE),
              ('huge', 1000.0::DOUBLE)
            ) values_(user_id, fit_score)"""
        )
        readiness = tmp_path / "readiness.parquet"
        _copy(con, "readiness_src", readiness)

        con.execute(
            """CREATE TABLE mobility_src AS SELECT user_id,
            0::BIGINT movement_events, NULL::DATE latest_assignment_date,
            NULL::VARCHAR latest_event_reason
            FROM readiness_src"""
        )
        mobility = tmp_path / "mobility.parquet"
        _copy(con, "mobility_src", mobility)
        con.execute(
            "CREATE TABLE risk AS "
            + _dataset_sql(
                "sap_successfactors_talent_retention_risk",
                {
                    "sap_successfactors_talent_readiness": readiness,
                    "sap_successfactors_talent_mobility_history": mobility,
                },
            )
        )
        assert (
            con.execute(
                "SELECT count(*) FROM risk WHERE status <> 'blocked' "
                "OR risk_band <> 'insufficient_data' OR retention_risk_score IS NOT NULL "
                "OR fit_score IS NOT NULL"
            ).fetchone()[0]
            == 0
        )
        risk = tmp_path / "risk.parquet"
        _copy(con, "risk", risk)

        con.execute(
            """CREATE TABLE roles_src AS SELECT 'JOB'::VARCHAR job_code,
            'ready'::VARCHAR required_skills_status"""
        )
        roles = tmp_path / "roles.parquet"
        _copy(con, "roles_src", roles)
        con.execute(
            "CREATE TABLE role_fit AS "
            + _dataset_sql(
                "sap_successfactors_talent_role_fit_assignments",
                {
                    "sap_successfactors_talent_readiness": readiness,
                    "sap_successfactors_talent_role_profile": roles,
                },
            )
        )
        assert (
            con.execute(
                "SELECT count(*) FROM role_fit WHERE status <> 'blocked' "
                "OR assignment_recommendation IS NOT NULL OR fit_score IS NOT NULL"
            ).fetchone()[0]
            == 0
        )
        role_fit = tmp_path / "role-fit.parquet"
        _copy(con, "role_fit", role_fit)

        empty_specs = {
            "nine_box": (
                "user_id VARCHAR, box_key VARCHAR, box_status VARCHAR, "
                "invalid_score_input BOOLEAN, "
                "performance_score DOUBLE, potential_score DOUBLE"
            ),
            "promotion": "box_key VARCHAR, misaligned_count BIGINT, status VARCHAR",
            "sensitivity": "near_cut_count BIGINT",
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
                    "sap_successfactors_talent_9box": paths["nine_box"],
                    "sap_successfactors_talent_mobility_history": mobility,
                    "sap_successfactors_talent_retention_risk": risk,
                    "sap_successfactors_talent_promotion_alignment": paths["promotion"],
                    "sap_successfactors_talent_calibration_sensitivity": paths[
                        "sensitivity"
                    ],
                    "sap_successfactors_talent_role_fit_assignments": role_fit,
                },
            )
        )
        assert con.execute("SELECT count(*) FROM candidates").fetchone()[0] == 0
        candidates = tmp_path / "candidates.parquet"
        _copy(con, "candidates", candidates)

        con.execute(
            "CREATE TABLE signals AS "
            + _dataset_sql(
                "sap_successfactors_talent_signals",
                {
                    "sap_successfactors_talent_action_candidates": candidates,
                    "sap_successfactors_talent_role_profile": roles,
                    "sap_successfactors_talent_mobility_history": mobility,
                },
            )
        )
        assert con.execute("SELECT count(*) FROM signals").fetchone()[0] == 0
    finally:
        con.close()
