from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def duck_engine_module():
    repo = Path(__file__).resolve().parents[1]
    sys.path[:] = [
        p for p in sys.path
        if not any(s in p for s in ("/cartridges/", "/console", "/vault",
                                     "/workspace", "/mcp-infra"))
    ]
    sys.path.insert(0, str(repo / "refinement"))
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    from app import duckdb_engine
    return duckdb_engine


def test_escape_doubles_single_quote(duck_engine_module):
    fn = duck_engine_module._escape_sql_literal_inner
    assert fn("2024-01-01' UNION SELECT 1--") == "2024-01-01'' UNION SELECT 1--"
    assert fn("a'b") == "a''b"


def test_escape_passes_through_clean_dates(duck_engine_module):
    fn = duck_engine_module._escape_sql_literal_inner
    assert fn("2024-01-01") == "2024-01-01"
    assert fn("1970-01-01") == "1970-01-01"


def test_escape_handles_none_and_empty(duck_engine_module):
    fn = duck_engine_module._escape_sql_literal_inner
    assert fn(None) == ""
    assert fn("") == ""


def test_escape_does_not_add_outer_quotes(duck_engine_module):
    fn = duck_engine_module._escape_sql_literal_inner
    out = fn("2024-01-01")
    assert not out.startswith("'")
    assert not out.endswith("'")


def _engine_with_resolved(monkeypatch, duck_engine_module, latest):
    Eng = duck_engine_module.DuckDBEngine
    eng = Eng.__new__(Eng)
    eng._resolve_latest_date = lambda source, user_context=None: latest
    return eng


def test_inject_latest_date_escapes_single_quote(monkeypatch, duck_engine_module):
    eng = _engine_with_resolved(monkeypatch, duck_engine_module,
                                latest="2024-01-01' UNION SELECT 1--")
    out = eng._inject_latest_date(
        "SELECT * FROM bronze WHERE load_date = '{latest_date}'",
        sources=["bronze.foo"],
    )
    assert "''" in out
    assert out == "SELECT * FROM bronze WHERE load_date = '2024-01-01'' UNION SELECT 1--'"


def test_inject_latest_date_clean_value_unchanged(monkeypatch, duck_engine_module):
    eng = _engine_with_resolved(monkeypatch, duck_engine_module,
                                latest="2024-05-15")
    out = eng._inject_latest_date(
        "SELECT * FROM bronze WHERE load_date = '{latest_date}'",
        sources=["bronze.foo"],
    )
    assert out == "SELECT * FROM bronze WHERE load_date = '2024-05-15'"


def test_inject_latest_date_no_placeholder_returns_unchanged(
    monkeypatch, duck_engine_module,
):
    eng = _engine_with_resolved(monkeypatch, duck_engine_module,
                                latest="anything")
    sql = "SELECT * FROM bronze WHERE 1=1"
    assert eng._inject_latest_date(sql, sources=["bronze.foo"]) == sql


def test_inject_latest_date_fallback_when_no_sources(
    monkeypatch, duck_engine_module,
):
    eng = _engine_with_resolved(monkeypatch, duck_engine_module, latest=None)
    out = eng._inject_latest_date(
        "WHERE load_date = '{latest_date}'", sources=[],
    )
    assert out == "WHERE load_date = '1970-01-01'"


def test_inject_latest_date_fallback_when_latest_resolves_none(
    monkeypatch, duck_engine_module,
):
    eng = _engine_with_resolved(monkeypatch, duck_engine_module, latest=None)
    out = eng._inject_latest_date(
        "WHERE load_date = '{latest_date}'", sources=["bronze.foo"],
    )
    assert out == "WHERE load_date = '1970-01-01'"


def test_consistent_quote_behaviour_with_sql_quote(duck_engine_module):
    raw = "a'b'c"
    via_sql_quote_inner = duck_engine_module._sql_quote(raw)
    via_inner_only      = duck_engine_module._escape_sql_literal_inner(raw)
    assert via_sql_quote_inner == "'" + via_inner_only + "'"


def test_injection_attempt_does_not_escape_literal(monkeypatch, duck_engine_module):
    payload = "2024-01-01'; DROP TABLE secrets; --"
    eng = _engine_with_resolved(monkeypatch, duck_engine_module, latest=payload)
    out = eng._inject_latest_date(
        "SELECT * FROM bronze WHERE load_date = '{latest_date}'",
        sources=["bronze.foo"],
    )
    inside_literal = "'2024-01-01''; DROP TABLE secrets; --'"
    assert out.endswith(inside_literal)
