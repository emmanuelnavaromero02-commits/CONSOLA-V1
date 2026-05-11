import pytest
from unittest.mock import MagicMock
import sys

sys.modules['duckdb'] = MagicMock()
sys.modules['psycopg2'] = MagicMock()

import os
os.environ['MINIO_SECRET_KEY'] = 'test'
os.environ['MINIO_ACCESS_KEY'] = 'test'
os.environ['MINIO_ENDPOINT'] = 'test'

from refinement.app.duckdb_engine import DuckDBEngine, _normalize_postgres_dsn, validate_safe_identifier

@pytest.fixture
def engine():
    e = DuckDBEngine()
    mock_conn = MagicMock()
    e._conn = MagicMock(return_value=mock_conn)
    yield e, mock_conn
    if e._con is not None:
        e._con.close()

def test_rls_default_deny(engine):
    e, mock_conn = engine
    mock_conn.execute.return_value.fetchall.return_value = [('some_col', 'varchar')]
    sql = "SELECT * FROM pggold.GOLD_USERS"
    ctx = {"email": "test@example.com"}

    rls_sql, params = e.get_rls_filters(sql, ctx)
    assert "WHERE 1=0" in rls_sql
    assert len(params) == 0

def test_rls_tenant_isolation(engine):
    e, mock_conn = engine
    mock_conn.execute.return_value.fetchall.return_value = [('tenant_id', 'varchar')]
    sql = "SELECT * FROM pggold.gold_sales"
    ctx = {"tenant_id": "tenant123"}

    rls_sql, params = e.get_rls_filters(sql, ctx)
    assert "tenant_id = ?" in rls_sql
    assert "tenant123" in params

def test_rls_intercepts_pggold_table_without_gold_prefix(engine):
    e, mock_conn = engine
    mock_conn.execute.return_value.fetchall.return_value = [('tenant_id', 'varchar')]
    sql = "SELECT * FROM pggold.billing"
    ctx = {"tenant_id": "tenant-billing"}

    rls_sql, params = e.get_rls_filters(sql, ctx)
    assert "pggold.billing" in rls_sql
    assert "tenant_id = ?" in rls_sql
    assert "tenant-billing" in params

def test_rls_workspace_isolation(engine):
    e, mock_conn = engine
    mock_conn.execute.return_value.fetchall.return_value = [('workspace_id', 'varchar')]
    sql = "SELECT * FROM pggold.gold_projects"
    ctx = {"workspace_id": "ws-456"}

    rls_sql, params = e.get_rls_filters(sql, ctx)
    assert "workspace_id = ?" in rls_sql
    assert "ws-456" in params

def test_rls_user_isolation(engine):
    e, mock_conn = engine
    mock_conn.execute.return_value.fetchall.return_value = [('user_id', 'varchar')]
    sql = "SELECT * FROM pggold.gold_timeentry"
    ctx = {"id": "user-789"}

    rls_sql, params = e.get_rls_filters(sql, ctx)
    assert "user_id = ?" in rls_sql
    assert "user-789" in params

@pytest.mark.parametrize("sql", [
    'SELECT * FROM pggold."gold_sales"',
    'SELECT * FROM "pggold"."gold_sales"',
    'SELECT * FROM "pggold".gold_sales',
    'SELECT * FROM pggold .gold_sales',
    'SELECT * FROM pggold\n.gold_sales',
    'SELECT * FROM pggold/*x*/.gold_sales',
    'SELECT * FROM pggold.gold_sales -- WHERE 1=1',
    'WITH x AS (SELECT * FROM pggold.gold_sales) SELECT * FROM x',
    'SELECT * FROM PGGOLD.gold_sales',
])
def test_rls_blocks_bypass_attempts(engine, sql):
    e, mock_conn = engine
    mock_conn.execute.return_value.fetchall.return_value = [('tenant_id', 'varchar')]
    ctx = {"tenant_id": "tenantA"}

    rls_sql, params = e.get_rls_filters(sql, ctx)

    assert "tenant_id = ?" in rls_sql, f"RLS bypass for: {sql!r} -> {rls_sql!r}"
    assert "tenantA" in params


def test_rls_admin_bypass(engine):
    e, mock_conn = engine
    mock_conn.execute.return_value.fetchall.return_value = [('tenant_id', 'varchar')]
    sql = "SELECT * FROM pggold.gold_sales"
    ctx = {"role": "admin", "_trusted_admin": True}

    rls_sql, params = e.get_rls_filters(sql, ctx)
    assert rls_sql == sql
    assert len(params) == 0


def test_rls_admin_role_alone_does_not_bypass(engine):
    """Defense in depth: a body that just claims `role=admin` (without the
    upstream-only `_trusted_admin` flag) must still be filtered. This blocks
    a peer service or compromised caller from forging admin via the request
    body alone — admin bypass requires the upstream service to opt in
    explicitly after authenticating its own user."""
    e, mock_conn = engine
    mock_conn.execute.return_value.fetchall.return_value = [('tenant_id', 'varchar')]
    sql = "SELECT * FROM pggold.gold_sales"
    ctx = {"role": "admin"}  # no _trusted_admin

    rls_sql, params = e.get_rls_filters(sql, ctx)
    assert "tenant_id = ?" in rls_sql, f"forged admin bypassed RLS: {rls_sql!r}"
    assert params == [""], "tenant filter should be applied with empty tenant"

def test_preview_sql_returns_schema_dicts(engine):
    e, mock_conn = engine
    cursor = MagicMock()
    cursor.description = [('customer_id', 'VARCHAR'), ('amount', 'DOUBLE')]
    cursor.fetchall.return_value = [('cust-1', 12.5)]
    mock_conn.execute.return_value = cursor

    result = e.preview_sql("SELECT customer_id, amount FROM pggold.gold_sales", user_context={"role": "admin", "_trusted_admin": True})

    assert result["schema"] == [
        {"name": "customer_id", "type": "VARCHAR"},
        {"name": "amount", "type": "DOUBLE"},
    ]
    assert result["data"] == [{"customer_id": "cust-1", "amount": 12.5}]
    assert result["row_count"] == 1

def test_preview_sql_rejects_dangerous_local_read(engine):
    e = DuckDBEngine()

    with pytest.raises(ValueError):
        e.preview_sql("SELECT * FROM read_csv('/etc/passwd')", user_context={"role": "admin", "_trusted_admin": True})

def test_valid_dataset_name_passes():
    validate_safe_identifier("gold_sales_2025", "dataset")

def test_normalize_postgres_dsn_sqlalchemy_driver():
    assert _normalize_postgres_dsn("postgresql+psycopg2://u:p@h/db") == "postgresql://u:p@h/db"

def test_normalize_postgres_dsn_native_postgres_url():
    assert _normalize_postgres_dsn("postgresql://u:p@h/db") == "postgresql://u:p@h/db"

@pytest.mark.parametrize("name", [
    'foo"; DROP TABLE x;--',
    "../secret",
    "foo/bar",
    "file:///etc/passwd",
    "foo bar",
])
def test_invalid_dataset_names_fail(name):
    with pytest.raises(ValueError):
        validate_safe_identifier(name, "dataset")

def test_silver_path_rejects_invalid_dataset_name():
    e = DuckDBEngine()

    with pytest.raises(ValueError):
        e._silver_path("replicon", "../secret")

def test_bronze_path_allows_expected_raw_source():
    e = DuckDBEngine()

    assert e._bronze_path("raw/replicon/TimeEntry") == "s3://lakehouse/raw/replicon/TimeEntry/**/*.parquet"

@pytest.mark.parametrize("source", [
    "../secret",
    "file:///etc/passwd",
    "raw/replicon/TimeEntry'; DROP TABLE x;--",
    "http://attacker/x",
])
def test_bronze_path_rejects_invalid_sources(source):
    e = DuckDBEngine()

    with pytest.raises(ValueError):
        e._bronze_path(source)

def test_bronze_read_rejects_invalid_source_before_sql_build():
    e = DuckDBEngine()

    with pytest.raises(ValueError):
        e._bronze_read("raw/replicon/TimeEntry'; DROP TABLE x;--")

def test_preview_sql_uses_duckdb_lock(engine):
    e, mock_conn = engine
    cursor = MagicMock()
    cursor.description = [('customer_id', 'VARCHAR')]
    cursor.fetchall.return_value = [('cust-1',)]
    mock_conn.execute.return_value = cursor

    class CountingLock:
        def __init__(self):
            self.entered = 0

        def __enter__(self):
            self.entered += 1

        def __exit__(self, exc_type, exc, tb):
            return False

    lock = CountingLock()
    e._duckdb_lock = lock

    result = e.preview_sql("SELECT customer_id FROM pggold.gold_sales", user_context={"role": "admin", "_trusted_admin": True})

    assert result["row_count"] == 1
    assert lock.entered == 1


# ── RLS + caller_params coexistence tests ─────────────────────────────────────

def test_preview_sql_applies_rls_even_when_caller_params_provided(engine):
    """RLS must NOT be skipped when the caller supplies params."""
    e, mock_conn = engine

    describe_cursor = MagicMock()
    describe_cursor.fetchall.return_value = [('tenant_id', 'varchar')]

    execute_cursor = MagicMock()
    execute_cursor.description = [('col', 'VARCHAR')]
    execute_cursor.fetchall.return_value = [('val',)]

    # First execute = DESCRIBE (inside get_rls_filters), second = SELECT
    mock_conn.execute.side_effect = [describe_cursor, execute_cursor]

    sql = "SELECT col FROM pggold.gold_sales WHERE col = ?"
    e.preview_sql(sql, params=["Garcia"], user_context={"tenant_id": "t-123"})

    # Final execute call must have combined_params = [rls_param, caller_param]
    final_call = mock_conn.execute.call_args_list[-1]
    _, combined_params = final_call[0]
    assert "t-123" in combined_params, "RLS tenant_id param missing from execute call"
    assert "Garcia" in combined_params, "Caller param missing from execute call"
    assert combined_params.index("t-123") < combined_params.index("Garcia"), \
        "RLS params must precede caller params (positional order)"


def test_preview_sql_rls_precedes_caller_params_positionally(engine):
    """Param order: rls_params first (inner subquery ?), then caller_params (outer WHERE ?)."""
    e, mock_conn = engine

    describe_cursor = MagicMock()
    describe_cursor.fetchall.return_value = [('workspace_id', 'varchar')]

    execute_cursor = MagicMock()
    execute_cursor.description = [('revenue', 'DOUBLE')]
    execute_cursor.fetchall.return_value = [(99.0,)]

    # first call = DESCRIBE, second call = actual SELECT
    mock_conn.execute.side_effect = [describe_cursor, execute_cursor]

    sql = "SELECT revenue FROM pggold.gold_sales WHERE year = ? AND month = ?"
    e.preview_sql(sql, params=["2025", "3"], user_context={"workspace_id": "ws-42"})

    actual_call = mock_conn.execute.call_args_list[-1]
    _, combined_params = actual_call[0]
    assert combined_params[0] == "ws-42",  "RLS workspace_id must be first param"
    assert combined_params[1] == "2025",   "First caller param must come after RLS"
    assert combined_params[2] == "3",      "Second caller param must be last"


def test_preview_sql_admin_with_caller_params_skips_rls_injection(engine):
    """Admin role bypasses RLS but still uses caller params unmodified."""
    e, mock_conn = engine

    cursor = MagicMock()
    cursor.description = [('col', 'VARCHAR')]
    cursor.fetchall.return_value = [('x',)]
    mock_conn.execute.return_value = cursor

    sql = "SELECT col FROM pggold.gold_sales WHERE col = ?"
    e.preview_sql(sql, params=["admin_value"], user_context={"role": "admin", "_trusted_admin": True})

    actual_call = mock_conn.execute.call_args_list[-1]
    _, combined_params = actual_call[0]
    # Admin: rls_params=[], caller_params=["admin_value"] → combined=["admin_value"]
    assert combined_params == ["admin_value"], \
        f"Admin should have only caller params, got: {combined_params}"


def test_query_dataset_does_not_double_apply_rls(engine):
    """query_dataset must not call get_rls_filters separately — preview_sql does it."""
    e, mock_conn = engine

    describe_cursor = MagicMock()
    describe_cursor.fetchall.return_value = [('tenant_id', 'varchar')]

    execute_cursor = MagicMock()
    execute_cursor.description = [('col', 'VARCHAR')]
    execute_cursor.fetchall.return_value = []

    mock_conn.execute.side_effect = [describe_cursor, execute_cursor]

    ds = {"name": "gold_sales", "sql_def": "SELECT col FROM pggold.gold_sales"}
    e.query_dataset(ds, filters={"col": "x"}, user_context={"tenant_id": "t-abc"})

    # Check the final SELECT call — tenant_id should appear exactly once
    final_sql_call = mock_conn.execute.call_args_list[-1][0][0]
    assert final_sql_call.count("tenant_id = ?") == 1, \
        f"RLS applied multiple times: {final_sql_call}"


def test_preview_sql_no_user_context_with_caller_params_defaults_to_deny(engine):
    """No user_context: get_rls_filters runs with empty ctx → deny-by-default for unrecognised tables."""
    e, mock_conn = engine

    # Table has 'some_col' only — not a recognised RLS column → 1=0 default deny
    describe_cursor = MagicMock()
    describe_cursor.fetchall.return_value = [('some_col', 'varchar')]

    execute_cursor = MagicMock()
    execute_cursor.description = [('col', 'VARCHAR')]
    execute_cursor.fetchall.return_value = []

    mock_conn.execute.side_effect = [describe_cursor, execute_cursor]

    sql = "SELECT col FROM pggold.gold_unknown WHERE col = ?"
    e.preview_sql(sql, params=["value"], user_context=None)

    final_sql = mock_conn.execute.call_args_list[-1][0][0]
    assert "1=0" in final_sql, "No user_context with unrecognised table must default to deny (WHERE 1=0)"
