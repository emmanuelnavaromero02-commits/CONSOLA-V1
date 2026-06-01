"""Sprint v1.41.0 — query_kb DuckDB guardrails.

Only SELECT / WITH statements should pass through. ATTACH, COPY,
INSTALL, LOAD, PRAGMA, and any DDL/DML keyword must be rejected
before reaching DuckDB.
"""
from __future__ import annotations

from tests.conftest import load_cartridge_app


def _call(sql: str, limit: int = 1):
    # Make sure replicon's ``app`` package is importable: load_cartridge_app
    # is the same helper test_replicon_mcp_auth uses, and it isolates sys.path
    # from any sibling service (mcp-infra/console/etc.) a previous test left
    # behind. We hit the bare ``query_kb`` function — the guard rails sit in
    # that function, before any DuckDB / MCP transport layer.
    load_cartridge_app("replicon")
    from app.mcp_server import query_kb as fn
    return fn(sql, limit)


def _message(res: dict) -> str:
    return f"{res.get('error', '')} {res.get('reason', '')}"


def test_query_kb_rejects_drop_table():
    res = _call("DROP TABLE foo")
    assert "error" in res
    assert "SELECT/WITH" in _message(res) or "Forbidden" in _message(res)


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
    assert "Multiple statements" in _message(res) or "ATTACH" in _message(res)


def test_query_kb_accepts_select():
    res = _call("SELECT 1 AS one")
    # The select itself may fail with DuckDB error (no MinIO mounted in
    # the test harness) but the guardrail must NOT short-circuit it.
    if "error" in res:
        assert "SELECT/WITH" not in _message(res)
        assert "Forbidden" not in _message(res)


def test_query_kb_accepts_with_cte():
    res = _call("WITH cte AS (SELECT 1 AS x) SELECT * FROM cte")
    if "error" in res:
        assert "SELECT/WITH" not in _message(res)
        assert "Forbidden" not in _message(res)
