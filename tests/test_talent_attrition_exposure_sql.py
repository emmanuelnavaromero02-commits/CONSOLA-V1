from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from tests.test_talent_nine_box_downstream import _copy, _dataset_sql, _quoted


ROOT = Path(__file__).resolve().parents[1]
DATASET = (
    ROOT
    / "cartridges/sap_successfactors/datasets"
    / "sap_successfactors_talent_attrition_exposure.sql"
)
_EMPTY_FEED_BLOCK = (
    "    SELECT\n"
    "        user_id,\n"
    "        CAST(NULL AS DECIMAL(18, 2)) AS annual_amount,\n"
    "        CAST(NULL AS VARCHAR) AS currency\n"
    "    FROM compensation_headers\n"
    "    WHERE FALSE"
)


@pytest.fixture()
def con():
    connection = duckdb.connect()
    try:
        yield connection
    finally:
        connection.close()


def _risk(con: duckdb.DuckDBPyConnection, tmp_path: Path, groups: dict[str, int]) -> Path:
    rows = []
    for department, size in groups.items():
        rows.extend(
            f"('{department}-{index}', '{department}', 'high', FALSE)"
            for index in range(size)
        )
    con.execute(
        "CREATE TABLE risk_src(user_id VARCHAR, department_name VARCHAR, "
        "risk_band VARCHAR, invalid_score_input BOOLEAN)"
    )
    con.execute("INSERT INTO risk_src VALUES " + ", ".join(rows))
    path = tmp_path / "risk.parquet"
    _copy(con, "risk_src", path)
    return path


def _headers(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> Path:
    con.execute(
        "CREATE TABLE headers_src AS SELECT DISTINCT user_id, 'PG'::VARCHAR pay_group "
        "FROM risk_src"
    )
    path = tmp_path / "headers.parquet"
    _copy(con, "headers_src", path)
    return path


def _exposure_sql(risk: Path, headers: Path) -> str:
    return _dataset_sql(
        "sap_successfactors_talent_attrition_exposure",
        {
            "sap_successfactors_empcompensation_latest": headers,
            "sap_successfactors_talent_retention_risk": risk,
        },
    )


def test_shipped_sql_yields_zero_rows_while_amounts_stay_encrypted(con, tmp_path):
    risk = _risk(con, tmp_path, {"Ventas": 6})
    headers = _headers(con, tmp_path)
    rows = con.execute(_exposure_sql(risk, headers)).fetchall()
    assert rows == []


def test_shipped_sql_never_touches_encrypted_compensation_columns():
    body = DATASET.read_text(encoding="utf-8").lower()
    assert "paycomp" not in body
    assert _EMPTY_FEED_BLOCK in DATASET.read_text(encoding="utf-8")


def _sql_with_cleared_amounts(con, tmp_path: Path, risk: Path, headers: Path) -> str:
    con.execute(
        """
        CREATE TABLE amounts_src AS
        SELECT user_id,
               CASE WHEN department_name = 'Mixta' AND user_id LIKE '%-0'
                    THEN 500000.0 ELSE 100000.0 END AS annual_amount,
               CASE WHEN department_name = 'Mixta' AND user_id LIKE '%-0'
                    THEN 'USD' ELSE 'MXN' END AS currency
        FROM risk_src
        """
    )
    amounts = tmp_path / "amounts.parquet"
    _copy(con, "amounts_src", amounts)
    sql = _exposure_sql(risk, headers)
    assert _EMPTY_FEED_BLOCK in sql
    return sql.replace(
        _EMPTY_FEED_BLOCK,
        "    SELECT user_id, annual_amount, currency "
        f"FROM read_parquet({_quoted(amounts)})",
        1,
    )


def test_group_rule_and_per_currency_aggregation(con, tmp_path):
    risk = _risk(con, tmp_path, {"Ventas": 6, "Chica": 4, "Mixta": 7})
    headers = _headers(con, tmp_path)
    sql = _sql_with_cleared_amounts(con, tmp_path, risk, headers)
    rows = con.execute(sql).fetchall()
    columns = [item[0] for item in con.execute(sql).description]
    assert "user_id" not in columns
    by_group = {(row[0], row[1], row[2]): row for row in rows}

    ventas = by_group[("Ventas", "high", "MXN")]
    assert ventas[3] == 6
    assert float(ventas[4]) == pytest.approx(600000.0)
    assert float(ventas[5]) == pytest.approx(100000.0)

    assert ("Chica", "high", "MXN") not in by_group
    assert ("Mixta", "high", "USD") not in by_group
    mixta = by_group[("Mixta", "high", "MXN")]
    assert mixta[3] == 6
    assert float(mixta[4]) == pytest.approx(600000.0)
    assert all(row[3] >= 5 for row in rows)
