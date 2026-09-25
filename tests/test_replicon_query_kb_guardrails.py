from __future__ import annotations

from contextlib import contextmanager

from tests.conftest import load_cartridge_app


@contextmanager
def _signed_scope():
    from app.core import request_context

    token = request_context.set_security_context(
        request_context._sign_security_context(
            {
                "trusted": True,
                "source": "console",
                "tenant_id": "tenant-1",
                "workspace_id": "ws-1",
            }
        )
    )
    try:
        yield
    finally:
        request_context.reset_security_context(token)


def _call(sql: str, limit: int = 1):
    load_cartridge_app("replicon")
    from app.mcp_server import query_kb as fn
    with _signed_scope():
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
    if "error" in res:
        assert "SELECT/WITH" not in _message(res)
        assert "Forbidden" not in _message(res)


def test_query_kb_accepts_with_cte():
    res = _call("WITH cte AS (SELECT 1 AS x) SELECT * FROM cte")
    if "error" in res:
        assert "SELECT/WITH" not in _message(res)
        assert "Forbidden" not in _message(res)
