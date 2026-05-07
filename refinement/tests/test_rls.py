import pytest
from unittest.mock import MagicMock
import sys

sys.modules['duckdb'] = MagicMock()
sys.modules['psycopg2'] = MagicMock()

import os
os.environ['MINIO_SECRET_KEY'] = 'test'
os.environ['MINIO_ACCESS_KEY'] = 'test'
os.environ['MINIO_ENDPOINT'] = 'test'

from refinement.app.duckdb_engine import DuckDBEngine

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
