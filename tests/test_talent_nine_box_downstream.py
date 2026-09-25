from __future__ import annotations

from pathlib import Path

import duckdb


ROOT = Path(__file__).resolve().parents[1]
DATASETS = ROOT / "cartridges/sap_successfactors/datasets"
MACRO = ROOT / "refinement/app/sql/talent_score_scale.sql"


def _quoted(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def _dataset_sql(name: str, sources: dict[str, Path]) -> str:
    sql = (DATASETS / f"{name}.sql").read_text(encoding="utf-8")
    for source, path in sources.items():
        for layer in ("gold", "silver"):
            uri = (
                f"'s3://{{bucket}}/{layer}/sap_successfactors/"
                f"{source}/**/*.parquet'"
            )
            sql = sql.replace(uri, _quoted(path))
    assert "s3://{bucket}" not in sql
    return sql


def _copy(con: duckdb.DuckDBPyConnection, table: str, path: Path) -> None:
    con.execute(f"COPY {table} TO {_quoted(path)} (FORMAT PARQUET)")


def test_invalid_9box_never_creates_promotion_candidate_or_signal(tmp_path):
    con = duckdb.connect()
    try:
        con.execute(MACRO.read_text(encoding="utf-8"))
        con.execute(
            """
            CREATE TABLE readiness_src AS SELECT
              'tenant-a'::VARCHAR tenant_id, 'workspace-a'::VARCHAR workspace_id,
              user_id, 'Persona'::VARCHAR full_name,
              'Empresa'::VARCHAR company_name, 'Area'::VARCHAR department_name,
              'Madrid'::VARCHAR location_name, 'JOB'::VARCHAR job_code,
              'Rol'::VARCHAR role_name, performance_score,
              80.0::DOUBLE competency_score, 80.0::DOUBLE aspiration_score,
              80.0::DOUBLE readiness_score, 'cpa_real'::VARCHAR source_mode,
              NULL::VARCHAR benchmark_version, FALSE benchmark_approval_valid,
              'not_applicable'::VARCHAR benchmark_provenance_status,
              invalid_score_input,
              'insufficient_data'::VARCHAR readiness_status,
              '["talent_score_input_invalid"]'::VARCHAR blockers
            FROM (VALUES
              ('employee-invalid', -1.0::DOUBLE, TRUE::BOOLEAN),
              ('employee-null-provenance', 80.0::DOUBLE, NULL::BOOLEAN)
            ) source(user_id, performance_score, invalid_score_input)
            """
        )
        readiness = tmp_path / "readiness.parquet"
        _copy(con, "readiness_src", readiness)

        con.execute(
            "CREATE TABLE nine_box AS "
            + _dataset_sql(
                "sap_successfactors_talent_9box",
                {"sap_successfactors_talent_readiness": readiness},
            )
        )
        assert con.execute(
            "SELECT invalid_score_input, box_status, box_key FROM nine_box "
            "ORDER BY user_id"
        ).fetchall() == [
            (True, "blocked", "insufficient_data"),
            (True, "blocked", "insufficient_data"),
        ]
        nine_box_path = tmp_path / "nine_box.parquet"
        _copy(con, "nine_box", nine_box_path)

        con.execute(
            "CREATE TABLE mobility AS SELECT user_id, "
            "'PROMOTION'::VARCHAR latest_event_reason, 0::BIGINT movement_events "
            "FROM readiness_src"
        )
        mobility = tmp_path / "mobility.parquet"
        _copy(con, "mobility", mobility)
        con.execute(
            "CREATE TABLE promotion AS "
            + _dataset_sql(
                "sap_successfactors_talent_promotion_alignment",
                {
                    "sap_successfactors_talent_9box": nine_box_path,
                    "sap_successfactors_talent_mobility_history": mobility,
                },
            )
        )
        assert con.execute(
            "SELECT misaligned_count, status FROM promotion WHERE box_key='summary'"
        ).fetchone() == (0, "partial")
        promotion = tmp_path / "promotion.parquet"
        _copy(con, "promotion", promotion)

        con.execute(
            "CREATE TABLE nine_operational AS "
            + _dataset_sql(
                "sap_successfactors_talent_9box_operational",
                {"sap_successfactors_talent_9box": nine_box_path},
            )
        )
        nine_operational = tmp_path / "nine_operational.parquet"
        _copy(con, "nine_operational", nine_operational)
        empty_specs = {
            "risk": (
                "risk_band VARCHAR, status VARCHAR, "
                "invalid_score_input BOOLEAN, fit_score DOUBLE"
            ),
            "sensitivity": "near_cut_count BIGINT",
            "role_fit": (
                "assignment_recommendation VARCHAR, status VARCHAR, "
                "invalid_score_input BOOLEAN, fit_score DOUBLE"
            ),
            "roles": "required_skills_status VARCHAR",
        }
        paths: dict[str, Path] = {}
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
                    "sap_successfactors_talent_9box": nine_box_path,
                    "sap_successfactors_talent_mobility_history": mobility,
                    "sap_successfactors_talent_retention_risk": paths["risk"],
                    "sap_successfactors_talent_promotion_alignment": promotion,
                    "sap_successfactors_talent_calibration_sensitivity": paths[
                        "sensitivity"
                    ],
                    "sap_successfactors_talent_role_fit_assignments": paths["role_fit"],
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
                    "sap_successfactors_talent_role_profile": paths["roles"],
                    "sap_successfactors_talent_mobility_history": mobility,
                },
            )
        )
        assert con.execute("SELECT count(*) FROM signals").fetchone()[0] == 0
    finally:
        con.close()


def test_profile_cpa_and_runtime_fallback_drop_only_invalid_scores(tmp_path):
    from refinement.app.successfactors_talent_core_fallbacks import (
        TALENT_CORE_FALLBACK_SQL,
    )

    con = duckdb.connect()
    try:
        con.execute(MACRO.read_text(encoding="utf-8"))
        con.execute(
            """CREATE TABLE employees AS
            SELECT 't' tenant_id, 'w' workspace_id, user_id, user_id full_name,
              'c' company_id, 'Company' company_name, 'd' division_id,
              'Division' division_name, 'dep' department_id,
              'Department' department_name, 'loc' location_id,
              'Madrid' location_name, 'JOB' job_code, NULL::VARCHAR manager_id,
              user_id user_id_hash, TRUE is_active, DATE '2020-01-01' start_date,
              NULL::DATE end_date
            FROM (VALUES ('invalid'), ('mixed')) v(user_id)"""
        )
        con.execute(
            "CREATE TABLE hierarchy (user_id VARCHAR, direct_reports BIGINT, depth BIGINT)"
        )
        con.execute(
            """CREATE TABLE performance AS SELECT * FROM (VALUES
              ('invalid', -1.0, 'ready'), ('mixed', -1.0, 'ready'),
              ('mixed', 5.0, 'ready'))
              v(user_id_hash, performance_rating, performance_status)"""
        )
        con.execute(
            """CREATE TABLE competency AS SELECT * FROM (VALUES
              ('invalid', -1.0, 'ready'), ('mixed', 100.0, 'ready'))
              v(user_id, proficiency_100, competency_status)"""
        )
        con.execute(
            """CREATE TABLE aspiration AS SELECT * FROM (VALUES
              ('invalid', -1.0, 'ready'), ('mixed', 100.0, 'ready'))
              v(user_id, aspiration_100, aspiration_status)"""
        )
        source_tables = {
            "sap_successfactors_employee_360": "employees",
            "sap_successfactors_manager_hierarchy": "hierarchy",
            "sap_successfactors_performance_cycle": "performance",
            "sap_successfactors_employee_competency": "competency",
            "sap_successfactors_employee_aspiration": "aspiration",
        }
        paths: dict[str, Path] = {}
        for source, table in source_tables.items():
            paths[source] = tmp_path / f"{source}.parquet"
            _copy(con, table, paths[source])
        con.execute(
            "CREATE TABLE profile AS "
            + _dataset_sql("sap_successfactors_talent_employee_profile", paths)
        )
        invalid = con.execute(
            "SELECT performance_score, competency_score, aspiration_score, "
            "cpa_status, blockers FROM profile WHERE user_id='invalid'"
        ).fetchone()
        assert invalid[:4] == (None, None, None, "blocked")
        assert all(
            name in invalid[4] for name in ("performance", "competency", "aspiration")
        )
        assert con.execute(
            "SELECT performance_score, competency_score, aspiration_score, cpa_status "
            "FROM profile WHERE user_id='mixed'"
        ).fetchone() == (100.0, 100.0, 100.0, "ready")

        profile_path = tmp_path / "profile.parquet"
        _copy(con, "profile", profile_path)
        con.execute(
            "CREATE TABLE roles AS SELECT 'JOB' job_code, 'Role' role_name, "
            "'ready' role_profile_status, 'ready' required_skills_status"
        )
        roles_path = tmp_path / "roles.parquet"
        _copy(con, "roles", roles_path)
        con.execute(
            "CREATE TABLE cpa AS "
            + _dataset_sql(
                "sap_successfactors_talent_cpa_scores",
                {
                    "sap_successfactors_talent_employee_profile": profile_path,
                    "sap_successfactors_talent_role_profile": roles_path,
                },
            )
        )
        assert con.execute(
            "SELECT performance_score, performance_100, fit_score, cpa_status "
            "FROM cpa WHERE user_id='mixed'"
        ).fetchone() == (100.0, 100.0, 100.0, "ready")

        fallback_sql = TALENT_CORE_FALLBACK_SQL[
            "sap_successfactors_talent_employee_profile"
        ]
        for source in (
            "sap_successfactors_employee_360",
            "sap_successfactors_performance_cycle",
        ):
            for layer in ("gold", "silver"):
                fallback_sql = fallback_sql.replace(
                    f"'s3://{{bucket}}/{layer}/sap_successfactors/{source}/**/*.parquet'",
                    _quoted(paths[source]),
                )
        con.execute("CREATE TABLE fallback_profile AS " + fallback_sql)
        assert con.execute(
            "SELECT performance_score, profile_status FROM fallback_profile "
            "WHERE user_id='invalid'"
        ).fetchone() == (None, "blocked")
        assert con.execute(
            "SELECT invalid_score_input, cpa_status FROM fallback_profile "
            "WHERE user_id='invalid'"
        ).fetchone() == (True, "blocked")
    finally:
        con.close()
