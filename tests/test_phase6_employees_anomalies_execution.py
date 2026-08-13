"""Fase 6 — executable proof that the Employee Central anomaly signal
materializes. Runs the REAL gold SQL (sap_successfactors_employees_anomalies.sql)
through DuckDB over synthetic employee_360 gold + fojobcode silver parquet.

Until now Phase-6 acceptance was 100% static (ordering/columns/labels); nothing
executed the SQL, so nobody could prove a real anomaly row actually lights up.
This closes that gap deterministically and reuses the DuckDB parquet harness
pattern from tests/test_talent_nine_box_downstream.py.
"""
from __future__ import annotations

from pathlib import Path

import duckdb

REPO = Path(__file__).resolve().parents[1]
DATASETS = REPO / "cartridges" / "sap_successfactors" / "datasets"
ANOMALIES = "sap_successfactors_employees_anomalies"
EXPECTED_COLUMNS = [
    "user_id",
    "full_name",
    "anomaly_type",
    "severity",
    "details",
    "detected_at",
]


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
    assert "s3://{bucket}" not in sql, "unsubstituted bucket URI remains"
    return sql


def _copy(con: duckdb.DuckDBPyConnection, table: str, path: Path) -> None:
    con.execute(f"COPY {table} TO {_quoted(path)} (FORMAT PARQUET)")


def _write_inputs(con: duckdb.DuckDBPyConnection, tmp_path: Path, employees_sql: str):
    con.execute(
        "CREATE TABLE emp360 AS SELECT * FROM (VALUES " + employees_sql + ") "
        "AS t(user_id, full_name, department_id, manager_id, job_code, is_active)"
    )
    con.execute(
        "CREATE TABLE fojob AS SELECT * FROM (VALUES ('J1'), ('J2')) AS t(job_code)"
    )
    emp_path = tmp_path / "emp360.parquet"
    fojob_path = tmp_path / "fojob.parquet"
    _copy(con, "emp360", emp_path)
    _copy(con, "fojob", fojob_path)
    return emp_path, fojob_path


def _run_anomalies(con, emp_path, fojob_path) -> None:
    con.execute(
        "CREATE TABLE anomalies AS "
        + _dataset_sql(
            ANOMALIES,
            {
                "sap_successfactors_employee_360": emp_path,
                "sap_successfactors_fojobcode_latest": fojob_path,
            },
        )
    )


def test_anomalies_materialize_three_types_and_exclude_clean_and_inactive(tmp_path):
    con = duckdb.connect()
    try:
        emp_path, fojob_path = _write_inputs(
            con,
            tmp_path,
            # clean active (no anomaly), blank dept, null manager, bad job code,
            # and an inactive employee that is dirty on every axis (must be excluded)
            "('u-clean',    'Clean Active', 'D1',  'M1',  'J1',   TRUE), "
            "('u-nodept',   'No Dept',      '',    'M1',  'J1',   TRUE), "
            "('u-nomgr',    'No Manager',   'D1',  NULL,  'J2',   TRUE), "
            "('u-badjob',   'Bad Jobcode',  'D1',  'M1',  'J999', TRUE), "
            "('u-inactive', 'Inactive',     NULL,  NULL,  'J999', FALSE)",
        )
        _run_anomalies(con, emp_path, fojob_path)

        rows = con.execute(
            "SELECT user_id, anomaly_type, severity FROM anomalies ORDER BY user_id"
        ).fetchall()
        assert rows == [
            ("u-badjob", "invalid_job_code", "high"),
            ("u-nodept", "missing_department", "medium"),
            ("u-nomgr", "missing_manager", "low"),
        ]

        # schema contract preserved
        cols = [c[0] for c in con.execute("DESCRIBE anomalies").fetchall()]
        assert cols == EXPECTED_COLUMNS
    finally:
        con.close()


def test_all_clean_input_yields_empty_result_with_schema_preserved(tmp_path):
    con = duckdb.connect()
    try:
        emp_path, fojob_path = _write_inputs(
            con,
            tmp_path,
            "('u-ok1', 'Fine One', 'D1', 'M1', 'J1', TRUE), "
            "('u-ok2', 'Fine Two', 'D2', 'M2', 'J2', TRUE)",
        )
        _run_anomalies(con, emp_path, fojob_path)

        assert con.execute("SELECT count(*) FROM anomalies").fetchone()[0] == 0
        cols = [c[0] for c in con.execute("DESCRIBE anomalies").fetchall()]
        assert cols == EXPECTED_COLUMNS
    finally:
        con.close()
