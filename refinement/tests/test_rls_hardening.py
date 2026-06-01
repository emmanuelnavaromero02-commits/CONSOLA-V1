"""Sprint v1.3 — RLS hardening tests.

Covers the four critical fixes against the auditor's report:
  CRIT-1 SQL injection in load_date interpolation
  CRIT-2 Incomplete dangerous-read blacklist
  CRIT-3 RLS bypass via CTE / UNION / subquery
  CRIT-4 Datasets without workspace_id (E2E, optional)

These tests must keep passing on every future change. Loosening any
assertion here is equivalent to weakening the RLS / SQL-safety guarantee.
"""
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


# ── CRIT-1: SQL injection in load_date ────────────────────────────────────

def test_load_date_sql_injection_blocked(engine):
    """A tampered load_date with a closing quote must NOT escape the literal.

    The sql_latest string is returned to the caller; if load_date contains
    "X' OR '1'='1", _sql_quote must produce 'X'' OR ''1''=''1' so the value
    stays a single SQL string literal.
    """
    e, mock_conn = engine
    mock_conn.execute.return_value.fetchall.return_value = [
        ("X' OR '1'='1", "batch-1"),
    ]
    result = e.get_source_partitions("raw/replicon/TimeEntry")
    sql = result.get("sql_latest", "")
    assert sql is not None
    # The full string literal must be present exactly once, with quotes doubled.
    assert "load_date = 'X'' OR ''1''=''1'" in sql, sql
    # And it must NOT be possible to terminate the literal early.
    assert "load_date = 'X' OR '1'='1'" not in sql


def test_sql_quote_doubles_single_quotes():
    assert _sql_quote("a'b") == "'a''b'"
    assert _sql_quote("") == "''"
    assert _sql_quote("normal") == "'normal'"


# ── CRIT-2: dangerous-read blacklist ──────────────────────────────────────

@pytest.mark.parametrize("sql,reason", [
    ("ATTACH 'postgres://attacker@evil/' AS evil",            "ATTACH"),
    ("DETACH evil",                                           "DETACH"),
    ("INSTALL httpfs",                                        "INSTALL"),
    ("LOAD httpfs",                                           "LOAD"),
    ("PRAGMA threads=4",                                      "PRAGMA"),
    ("COPY (SELECT 1) TO '/tmp/x'",                           "no-op"),  # COPY TO is allowed (no FROM)
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
    if reason == "no-op":
        # COPY ... TO ... is fine — only COPY ... FROM is blocked.
        e._validate_safe_sql(sql)
        return
    with pytest.raises(ValueError) as excinfo:
        e._validate_safe_sql(sql)
    assert "SQL blocked by safety policy" in str(excinfo.value)


def test_read_parquet_s3_allowed(engine):
    e, _ = engine
    # read_parquet on s3:// must still pass — it's how refinement reads bronze.
    e._validate_safe_sql("SELECT * FROM read_parquet('s3://lakehouse/raw/replicon/X/**/*.parquet')")


def test_read_parquet_local_path_blocked(engine):
    e, _ = engine
    with pytest.raises(ValueError):
        e._validate_safe_sql("SELECT * FROM read_parquet('/etc/passwd.parquet')")


# ── CRIT-3: AST-based RLS — bypass via CTE / UNION / subquery ─────────────

def _describe_cols(*cols):
    """Helper that mimics what DESCRIBE returns for a pggold table."""
    return [(c, "varchar") for c in cols]


def test_rls_inject_cte(engine):
    """The classic bypass (`WITH x AS (SELECT * FROM pggold.gold_sales) ...`)
    must filter the inner reference, not the outer alias."""
    e, mock_conn = engine
    mock_conn.execute.return_value.fetchall.return_value = _describe_cols("workspace_id")
    sql = "WITH x AS (SELECT * FROM pggold.gold_sales) SELECT * FROM x"
    rls_sql, params = e.get_rls_filters(sql, {"workspace_id": "ws-1"})
    assert "workspace_id = ?" in rls_sql, rls_sql
    assert "pggold.gold_sales" in rls_sql
    assert params == ["ws-1"]


def test_rls_inject_union(engine):
    """Both branches of a UNION must be filtered."""
    e, mock_conn = engine
    mock_conn.execute.return_value.fetchall.return_value = _describe_cols("tenant_id", "workspace_id")
    sql = "SELECT * FROM pggold.t1 UNION ALL SELECT * FROM pggold.t2"
    rls_sql, params = e.get_rls_filters(sql, {"tenant_id": "t-A", "workspace_id": "ws-A"})
    # Two pggold branches, each requiring tenant + workspace.
    assert rls_sql.count("tenant_id = ? AND workspace_id = ?") == 2, rls_sql
    assert params == ["t-A", "ws-A", "t-A", "ws-A"]


def test_rls_inject_subquery(engine):
    """A FROM (SELECT ... FROM pggold.X) wrapper must still filter pggold.X."""
    e, mock_conn = engine
    mock_conn.execute.return_value.fetchall.return_value = _describe_cols("tenant_id", "workspace_id")
    sql = "SELECT * FROM (SELECT * FROM pggold.t1) sub"
    rls_sql, params = e.get_rls_filters(sql, {"tenant_id": "t-A", "workspace_id": "ws-A"})
    assert "tenant_id = ? AND workspace_id = ?" in rls_sql
    assert params == ["t-A", "ws-A"]


def test_rls_inject_alias_preserved(engine):
    """Original table aliases must survive the rewrite so qualified refs
    like `gs.col` keep resolving in the outer query."""
    e, mock_conn = engine
    mock_conn.execute.return_value.fetchall.return_value = _describe_cols("workspace_id")
    sql = "SELECT gs.col FROM pggold.gold_sales AS gs WHERE gs.col = 1"
    rls_sql, _params = e.get_rls_filters(sql, {"workspace_id": "ws-X"})
    # The subquery wrapper must still be aliased `gs` so `gs.col` works.
    assert "AS gs" in rls_sql or "AS \"gs\"" in rls_sql, rls_sql


def test_sqlglot_parse_failure_denies(engine):
    """If sqlglot cannot parse the SQL, the query must be refused — there
    is no silent fallback to the legacy regex path."""
    e, _ = engine
    with pytest.raises(ValueError) as excinfo:
        e.get_rls_filters("GIBBERISH NOT SQL!!!", {"workspace_id": "ws-1"})
    assert "default-deny" in str(excinfo.value).lower()


def test_rls_default_deny_when_no_tenancy_column(engine):
    """If a pggold table has no recognised tenancy column, default-deny
    must still apply (WHERE 1=0) even with the AST rewrite."""
    e, mock_conn = engine
    mock_conn.execute.return_value.fetchall.return_value = _describe_cols("some_random_col")
    rls_sql, params = e.get_rls_filters(
        "SELECT * FROM pggold.unknown_table", {"workspace_id": "ws-1"}
    )
    # Whitespace-normalised check ("1 = 0" → "1=0").
    assert "1=0" in rls_sql.replace(" ", ""), rls_sql
    assert params == []


def test_rls_admin_trusted_bypasses_ast(engine):
    """Backend-trusted admin bypass must short-circuit before AST
    rewriting — protects legitimate admin queries from any sqlglot
    quirks."""
    e, _ = engine
    sql = "SELECT * FROM pggold.gold_sales"
    rls_sql, params = e.get_rls_filters(
        sql, {"role": "admin", "_server_trusted_context": True}
    )
    assert rls_sql == sql
    assert params == []
