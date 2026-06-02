"""Regression test for P0-RLS-001.

A workspace_admin can submit a gold dataset whose SQL emits literal
``tenant_id`` / ``workspace_id`` columns. The materializer's
``_ensure_scope_columns`` must OVERRIDE any caller-supplied scope with the
server-derived session scope (not merely add it when absent), so a caller
cannot forge another tenant's scope into the shared ``gold_<name>`` table.

These tests run a real in-memory DuckDB (no Docker / no Postgres needed).
"""
import importlib
import os
import sys

import pytest

os.environ.setdefault("MINIO_SECRET_KEY", "test")
os.environ.setdefault("MINIO_ACCESS_KEY", "test")
os.environ.setdefault("MINIO_ENDPOINT", "test")


def _real_duckdb():
    """Return the real duckdb module even if a sibling test mocked it."""
    mod = sys.modules.get("duckdb")
    if mod is not None and type(mod).__module__.startswith("unittest.mock"):
        sys.modules.pop("duckdb", None)
        importlib.invalidate_caches()
    return importlib.import_module("duckdb")


@pytest.fixture
def engine_and_con():
    duckdb = _real_duckdb()
    from refinement.app.duckdb_engine import DuckDBEngine

    e = DuckDBEngine()
    con = duckdb.connect()
    try:
        yield e, con
    finally:
        con.close()


def _run(con, sql):
    cur = con.execute(sql)
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    return cols, rows


def test_forged_scope_columns_are_overridden(engine_and_con):
    e, con = engine_and_con
    ctx = {"tenant_id": "good_tenant", "workspace_id": "good_ws"}
    forged = "SELECT 'EVIL_TENANT' AS tenant_id, 'EVIL_WS' AS workspace_id, 42 AS v"
    out = e._ensure_scope_columns(con, forged, ctx)
    cols, rows = _run(con, out)
    rec = dict(zip(cols, rows[0]))
    # forged scope must be discarded, server scope enforced
    assert rec["tenant_id"] == "good_tenant"
    assert rec["workspace_id"] == "good_ws"
    # the real payload survives untouched
    assert rec["v"] == 42
    # exactly one of each scope column (no duplicates from the rewrite)
    assert cols.count("tenant_id") == 1
    assert cols.count("workspace_id") == 1


def test_missing_scope_columns_are_added(engine_and_con):
    e, con = engine_and_con
    ctx = {"tenant_id": "t1", "workspace_id": "w1"}
    out = e._ensure_scope_columns(con, "SELECT 7 AS v", ctx)
    cols, rows = _run(con, out)
    rec = dict(zip(cols, rows[0]))
    assert rec["tenant_id"] == "t1"
    assert rec["workspace_id"] == "w1"
    assert rec["v"] == 7


def test_partial_forge_only_tenant_present(engine_and_con):
    e, con = engine_and_con
    ctx = {"tenant_id": "t1", "workspace_id": "w1"}
    out = e._ensure_scope_columns(con, "SELECT 'EVIL' AS tenant_id, 9 AS v", ctx)
    cols, rows = _run(con, out)
    rec = dict(zip(cols, rows[0]))
    assert rec["tenant_id"] == "t1"      # present -> overridden
    assert rec["workspace_id"] == "w1"   # missing -> added
    assert rec["v"] == 9


def test_partial_forge_only_workspace_present(engine_and_con):
    e, con = engine_and_con
    ctx = {"tenant_id": "t1", "workspace_id": "w1"}
    out = e._ensure_scope_columns(con, "SELECT 'EVIL' AS workspace_id, 9 AS v", ctx)
    cols, rows = _run(con, out)
    rec = dict(zip(cols, rows[0]))
    assert rec["workspace_id"] == "w1"   # present -> overridden
    assert rec["tenant_id"] == "t1"      # missing -> added
    assert rec["v"] == 9


def test_mixed_case_forged_columns_are_overridden(engine_and_con):
    e, con = engine_and_con
    ctx = {"tenant_id": "t1", "workspace_id": "w1"}
    forged = "SELECT 'EVIL' AS \"Tenant_Id\", 'EVIL2' AS \"Workspace_Id\", 3 AS v"
    out = e._ensure_scope_columns(con, forged, ctx)
    cols, rows = _run(con, out)
    rec = {str(k).lower(): v for k, v in zip(cols, rows[0])}
    assert rec["tenant_id"] == "t1"
    assert rec["workspace_id"] == "w1"
    assert rec["v"] == 3
    # case-insensitive REPLACE must not duplicate the scope columns
    assert sum(1 for c in cols if str(c).lower() == "tenant_id") == 1
    assert sum(1 for c in cols if str(c).lower() == "workspace_id") == 1


def test_no_scope_context_is_passthrough(engine_and_con):
    e, con = engine_and_con
    out = e._ensure_scope_columns(con, "SELECT 1 AS v", {})
    assert out == "SELECT 1 AS v"


def test_named_insert_aligns_by_name_not_position(engine_and_con):
    """SQL-correctness audit regression: a gold table whose physical column order
    differs from the producing SELECT must still load correctly because the
    materialize INSERT now lists columns by NAME (via _quoted_columns), not a
    positional SELECT *. A positional insert would mis-map a -> tenant_id."""
    e, con = engine_and_con
    con.execute("CREATE TABLE g (tenant_id TEXT, workspace_id TEXT, a INTEGER, b TEXT)")
    producing = "SELECT 5 AS a, 't1' AS tenant_id, 'x' AS b, 'w1' AS workspace_id"
    col_list = e._quoted_columns(con, producing)
    con.execute(f"INSERT INTO g ({col_list}) SELECT {col_list} FROM ({producing}) _q")
    cols = [d[0] for d in con.execute("SELECT * FROM g").description]
    rec = dict(zip(cols, con.execute("SELECT * FROM g").fetchall()[0]))
    assert rec["tenant_id"] == "t1"
    assert rec["workspace_id"] == "w1"
    assert rec["a"] == 5
    assert rec["b"] == "x"
