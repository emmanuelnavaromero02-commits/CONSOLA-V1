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

def test_rls_admin_bypass(engine):
    e, mock_conn = engine
    mock_conn.execute.return_value.fetchall.return_value = [('tenant_id', 'varchar')]
    sql = "SELECT * FROM pggold.gold_sales"
    ctx = {"role": "admin"}

    rls_sql, params = e.get_rls_filters(sql, ctx)
    assert rls_sql == sql
    assert len(params) == 0

def test_preview_sql_returns_schema_dicts(engine):
    e, mock_conn = engine
    cursor = MagicMock()
    cursor.description = [('customer_id', 'VARCHAR'), ('amount', 'DOUBLE')]
    cursor.fetchall.return_value = [('cust-1', 12.5)]
    mock_conn.execute.return_value = cursor

    result = e.preview_sql("SELECT customer_id, amount FROM pggold.gold_sales", user_context={"role": "admin"})

    assert result["schema"] == [
        {"name": "customer_id", "type": "VARCHAR"},
        {"name": "amount", "type": "DOUBLE"},
    ]
    assert result["data"] == [{"customer_id": "cust-1", "amount": 12.5}]
    assert result["row_count"] == 1

def test_preview_sql_rejects_dangerous_local_read(engine):
    e = DuckDBEngine()

    with pytest.raises(ValueError):
        e.preview_sql("SELECT * FROM read_csv('/etc/passwd')", user_context={"role": "admin"})

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

    result = e.preview_sql("SELECT customer_id FROM pggold.gold_sales", user_context={"role": "admin"})

    assert result["row_count"] == 1
    assert lock.entered == 1
