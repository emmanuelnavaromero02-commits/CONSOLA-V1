"""Sprint v1.41.0 — query_kb DuckDB guardrails.

Only SELECT / WITH statements should pass through. ATTACH, COPY,
INSTALL, LOAD, PRAGMA, and any DDL/DML keyword must be rejected
before reaching DuckDB.
"""
from __future__ import annotations

from tests.conftest import load_cartridge_app


def _query_kb():
    main = load_cartridge_app("replicon")
    # ``mcp.tool()`` wraps the function; the original is at .fn on the
    # registered tool. Skip the wrapper to call the function directly
    # without exercising the MCP transport / auth layer.
    return main.mcp


def _call(sql: str, limit: int = 1):
    import asyncio
    mcp = _query_kb()
    tool = asyncio.get_event_loop().run_until_complete(mcp.get_tool("query_kb")) \
        if hasattr(mcp, "get_tool") else None
    # Fall back: import the function directly from the module
    from app.mcp_server import query_kb as fn
    return fn(sql, limit)


def test_query_kb_rejects_drop_table():
    res = _call("DROP TABLE foo")
    assert "error" in res
    assert "SELECT/WITH" in res["error"] or "Forbidden" in res["error"]


def test_query_kb_rejects_attach():
    res = _call("ATTACH 'foo.db' AS x")
    assert "error" in res


def test_query_kb_rejects_insert():
    res = _call("INSERT INTO foo VALUES (1)")
    assert "error" in res


def test_query_kb_rejects_pragma():
    res = _call("PRAGMA database_list")
    assert "error" in res


def test_query_kb_rejects_install():
    res = _call("INSTALL httpfs")
    assert "error" in res


def test_query_kb_rejects_select_followed_by_attach():
    res = _call("SELECT 1; ATTACH 'foo.db' AS x")
    assert "error" in res
    assert "ATTACH" in res["error"]


def test_query_kb_accepts_select():
    res = _call("SELECT 1 AS one")
    # The select itself may fail with DuckDB error (no MinIO mounted in
    # the test harness) but the guardrail must NOT short-circuit it.
    if "error" in res:
        assert "SELECT/WITH" not in res["error"]
        assert "Forbidden" not in res["error"]


def test_query_kb_accepts_with_cte():
    res = _call("WITH cte AS (SELECT 1 AS x) SELECT * FROM cte")
    if "error" in res:
        assert "SELECT/WITH" not in res["error"]
        assert "Forbidden" not in res["error"]
