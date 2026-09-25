from __future__ import annotations

import duckdb
import pytest

from tests.test_talent_nine_box_downstream import MACRO, _copy, _dataset_sql


@pytest.mark.parametrize(
    "literal",
    [
        "TRUE",
        "FALSE",
        "json('true')",
        "json('false')",
        "'-1e-400'",
        "'-1e-324'",
        "'100.00000000000000000000000000000000001'",
        "'75'",
    ],
)
def test_castable_semantic_types_are_not_numeric_scores(literal):
    con = duckdb.connect()
    try:
        con.execute(MACRO.read_text(encoding="utf-8"))
        assert (
            con.execute(f"SELECT talent_score_scale({literal})").fetchone()[0] is None
        )
        assert (
            con.execute(f"SELECT talent_percent_scale({literal})").fetchone()[0] is None
        )
    finally:
        con.close()


def test_decimal_range_is_checked_before_conversion_to_double():
    con = duckdb.connect()
    try:
        con.execute(MACRO.read_text(encoding="utf-8"))
        assert (
            con.execute(
                "SELECT talent_score_scale("
                "100.00000000000000000000000000000000001::DECIMAL(38,35))"
            ).fetchone()[0]
            is None
        )
        assert (
            con.execute(
                "SELECT talent_score_scale("
                "-0.00000000000000000000000000000000001::DECIMAL(38,35))"
            ).fetchone()[0]
            is None
        )
        assert (
            con.execute("SELECT talent_score_scale(100.0::DECIMAL(38,35))").fetchone()[
                0
            ]
            == 5.0
        )
    finally:
        con.close()


@pytest.mark.parametrize("source_expression", ["TRUE", "json('true')"])
def test_semantic_sources_block_the_profile_before_cpa_or_9box(
    tmp_path, source_expression
):
    con = duckdb.connect()
    try:
        con.execute(MACRO.read_text(encoding="utf-8"))
        con.execute(
            """CREATE TABLE employees AS SELECT
            't' tenant_id, 'w' workspace_id, 'employee' user_id,
            'Persona' full_name, 'c' company_id, 'Empresa' company_name,
            'd' division_id, 'Division' division_name, 'dep' department_id,
            'Area' department_name, 'loc' location_id, 'Madrid' location_name,
            'JOB' job_code, NULL::VARCHAR manager_id, 'hash' user_id_hash,
            TRUE is_active, DATE '2020-01-01' start_date, NULL::DATE end_date"""
        )
        con.execute(
            "CREATE TABLE hierarchy (user_id VARCHAR, direct_reports BIGINT, depth BIGINT)"
        )
        con.execute(
            "CREATE TABLE performance AS SELECT 'hash' user_id_hash, "
            f"{source_expression} performance_rating, 'ready' performance_status"
        )
        con.execute(
            "CREATE TABLE competency AS SELECT 'employee' user_id, "
            f"{source_expression} proficiency_100, 'ready' competency_status"
        )
        con.execute(
            "CREATE TABLE aspiration AS SELECT 'employee' user_id, "
            f"{source_expression} aspiration_100, 'ready' aspiration_status"
        )
        sources = {}
        for source, table in {
            "sap_successfactors_employee_360": "employees",
            "sap_successfactors_manager_hierarchy": "hierarchy",
            "sap_successfactors_performance_cycle": "performance",
            "sap_successfactors_employee_competency": "competency",
            "sap_successfactors_employee_aspiration": "aspiration",
        }.items():
            path = tmp_path / f"{source}.parquet"
            _copy(con, table, path)
            sources[source] = path
        con.execute(
            "CREATE TABLE profile AS "
            + _dataset_sql("sap_successfactors_talent_employee_profile", sources)
        )
        assert con.execute(
            "SELECT competency_score, performance_score, aspiration_score, "
            "invalid_score_input, cpa_status FROM profile"
        ).fetchone() == (None, None, None, True, "blocked")
    finally:
        con.close()
