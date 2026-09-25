import sys
from unittest.mock import MagicMock

import pytest

sys.modules.setdefault('duckdb', MagicMock())
sys.modules.setdefault('psycopg2', MagicMock())

import os
os.environ.setdefault('MINIO_SECRET_KEY', 'test')
os.environ.setdefault('MINIO_ACCESS_KEY', 'test')
os.environ.setdefault('MINIO_ENDPOINT', 'test')

from refinement.app.duckdb_engine import DuckDBEngine, _sql_quote


@pytest.fixture
def engine():
    e = DuckDBEngine()
    mock_conn = MagicMock()
    e._conn = MagicMock(return_value=mock_conn)
    yield e, mock_conn


def test_load_date_sql_injection_blocked(engine):
    e, mock_conn = engine
    mock_conn.execute.return_value.fetchall.return_value = [
        ("X' OR '1'='1", "batch-1"),
    ]
    result = e.get_source_partitions("raw/replicon/TimeEntry")
    sql = result.get("sql_latest", "")
    assert sql is not None
    assert "load_date = 'X'' OR ''1''=''1'" in sql, sql
    assert "load_date = 'X' OR '1'='1'" not in sql


def test_sql_quote_doubles_single_quotes():
    assert _sql_quote("a'b") == "'a''b'"
    assert _sql_quote("") == "''"
    assert _sql_quote("normal") == "'normal'"


@pytest.mark.parametrize("sql,reason", [
    ("ATTACH 'postgres://attacker@evil/' AS evil",            "ATTACH"),
    ("DETACH evil",                                           "DETACH"),
    ("INSTALL httpfs",                                        "INSTALL"),
    ("LOAD httpfs",                                           "LOAD"),
    ("PRAGMA threads=4",                                      "PRAGMA"),
    ("COPY (SELECT 1) TO '/tmp/x'",                           "COPY TO"),
    ("COPY t FROM '/etc/passwd'",                             "COPY FROM"),
    ("SET memory_limit='1GB'",                                "SET memory_limit"),
    ("SET GLOBAL foo=1",                                      "SET GLOBAL"),
    ("CREATE TABLE evil AS SELECT 1",                         "CREATE TABLE"),
    ("CREATE VIEW evil AS SELECT 1",                          "CREATE VIEW"),
    ("CREATE SECRET s (TYPE s3)",                             "CREATE SECRET"),
    ("DROP TABLE pggold.gold_sales",                          "DROP TABLE"),
    ("SELECT read_blob('/etc/passwd')",                       "read_blob"),
    ("SELECT read_text('/etc/passwd')",                       "read_text"),
    ("SELECT read_json('/etc/passwd')",                       "read_json"),
    ("SELECT read_csv('/etc/passwd')",                        "read_csv"),
    ("SELECT * FROM read_parquet_objects('s3://x')",          "read_parquet_objects"),
])
def test_dangerous_patterns_blocked(engine, sql, reason):
    e, _ = engine
    with pytest.raises(ValueError) as excinfo:
        e._validate_safe_sql(sql)
    assert "SQL blocked by safety policy" in str(excinfo.value)


def test_read_parquet_s3_allowed(engine):
    e, _ = engine
    e._validate_safe_sql("SELECT * FROM read_parquet('s3://lakehouse/raw/replicon/X/**/*.parquet')")


def test_read_parquet_local_path_blocked(engine):
    e, _ = engine
    with pytest.raises(ValueError):
        e._validate_safe_sql("SELECT * FROM read_parquet('/etc/passwd.parquet')")


def _describe_cols(*cols):
    return [(c, "varchar") for c in cols]


def test_rls_inject_cte(engine):
    e, mock_conn = engine
    mock_conn.execute.return_value.fetchall.return_value = _describe_cols("workspace_id")
    sql = "WITH x AS (SELECT * FROM pggold.gold_sales) SELECT * FROM x"
    rls_sql, params = e.get_rls_filters(sql, {"workspace_id": "ws-1"})
    assert "workspace_id = ?" in rls_sql, rls_sql
    assert "pggold.gold_sales" in rls_sql
    assert params == ["ws-1"]


def test_rls_inject_union(engine):
    e, mock_conn = engine
    mock_conn.execute.return_value.fetchall.return_value = _describe_cols("tenant_id", "workspace_id")
    sql = "SELECT * FROM pggold.t1 UNION ALL SELECT * FROM pggold.t2"
    rls_sql, params = e.get_rls_filters(sql, {"tenant_id": "t-A", "workspace_id": "ws-A"})
    assert rls_sql.count("tenant_id = ? AND workspace_id = ?") == 2, rls_sql
    assert params == ["t-A", "ws-A", "t-A", "ws-A"]


def test_rls_inject_subquery(engine):
    e, mock_conn = engine
    mock_conn.execute.return_value.fetchall.return_value = _describe_cols("tenant_id", "workspace_id")
    sql = "SELECT * FROM (SELECT * FROM pggold.t1) sub"
    rls_sql, params = e.get_rls_filters(sql, {"tenant_id": "t-A", "workspace_id": "ws-A"})
    assert "tenant_id = ? AND workspace_id = ?" in rls_sql
    assert params == ["t-A", "ws-A"]


def test_rls_inject_alias_preserved(engine):
    e, mock_conn = engine
    mock_conn.execute.return_value.fetchall.return_value = _describe_cols("workspace_id")
    sql = "SELECT gs.col FROM pggold.gold_sales AS gs WHERE gs.col = 1"
    rls_sql, _params = e.get_rls_filters(sql, {"workspace_id": "ws-X"})
    assert "AS gs" in rls_sql or "AS \"gs\"" in rls_sql, rls_sql


def test_sqlglot_parse_failure_denies(engine):
    e, _ = engine
    with pytest.raises(ValueError) as excinfo:
        e.get_rls_filters("GIBBERISH NOT SQL!!!", {"workspace_id": "ws-1"})
    assert "default-deny" in str(excinfo.value).lower()


def test_rls_default_deny_when_no_tenancy_column(engine):
    e, mock_conn = engine
    mock_conn.execute.return_value.fetchall.return_value = _describe_cols("some_random_col")
    rls_sql, params = e.get_rls_filters(
        "SELECT * FROM pggold.unknown_table", {"workspace_id": "ws-1"}
    )
    assert "1=0" in rls_sql.replace(" ", ""), rls_sql
    assert params == []


def test_rls_admin_trusted_bypasses_ast(engine):
    e, _ = engine
    sql = "SELECT * FROM pggold.gold_sales"
    rls_sql, params = e.get_rls_filters(
        sql, {"role": "admin", "_server_trusted_context": True}
    )
    assert rls_sql == sql
    assert params == []
