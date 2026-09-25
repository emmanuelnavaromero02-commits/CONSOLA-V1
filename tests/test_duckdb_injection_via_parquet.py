"""Sprint v1.43.1 — B4: SQL injection via Parquet load_date.

``_inject_latest_date`` substitutes ``{latest_date}`` into LLM-generated
templates of the form ``WHERE load_date = '{latest_date}'``. Before
v1.43.1 the substitution was raw — a malicious ``load_date`` value
written into a Parquet file in MinIO could break out of the literal and
inject arbitrary SQL.

These tests verify the escape now neutralises single quotes embedded in
the value, while preserving the template's existing outer quotes.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def duck_engine_module():
    repo = Path(__file__).resolve().parents[1]
    # Refinement's app/ shadows console/app/; isolate it.
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


# ── _escape_sql_literal_inner: pure unit tests ─────────────────────────────

def test_escape_doubles_single_quote(duck_engine_module):
    fn = duck_engine_module._escape_sql_literal_inner
    # The classic SQL injection payload: a single quote breaks out of the
    # literal. We must double it so the parser sees one literal quote.
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
    """The template (llm_sql.py:20) already wraps {latest_date} in
    single quotes. Adding more would produce '' '2024-01-01' '' →
    broken. This helper is distinct from _sql_quote precisely for
    that case."""
    fn = duck_engine_module._escape_sql_literal_inner
    out = fn("2024-01-01")
    assert not out.startswith("'")
    assert not out.endswith("'")


# ── _inject_latest_date: end-to-end on the SQL string ──────────────────────

def _engine_with_resolved(monkeypatch, duck_engine_module, latest):
    """Build a DuckDBEngine and stub _resolve_latest_date to return
    ``latest`` (string or None)."""
    Eng = duck_engine_module.DuckDBEngine
    eng = Eng.__new__(Eng)  # bypass __init__ (config)
    eng._resolve_latest_date = lambda source, user_context=None: latest
    return eng


def test_inject_latest_date_escapes_single_quote(monkeypatch, duck_engine_module):
    """The hostile path: a Parquet load_date with an embedded quote.
    After injection, the SQL must still be a single well-formed
    literal — the quote is doubled so DuckDB reads it as data."""
    eng = _engine_with_resolved(monkeypatch, duck_engine_module,
                                latest="2024-01-01' UNION SELECT 1--")
    out = eng._inject_latest_date(
        "SELECT * FROM bronze WHERE load_date = '{latest_date}'",
        sources=["bronze.foo"],
    )
    # The injected fragment must contain a doubled quote and no bare
    # quote in the middle.
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
    """When no sources are provided, the fallback ``1970-01-01`` is
    injected. The escape is applied for symmetry even though there's
    no quote to neutralise."""
    eng = _engine_with_resolved(monkeypatch, duck_engine_module, latest=None)
    out = eng._inject_latest_date(
        "WHERE load_date = '{latest_date}'", sources=[],
    )
    assert out == "WHERE load_date = '1970-01-01'"


def test_inject_latest_date_fallback_when_latest_resolves_none(
    monkeypatch, duck_engine_module,
):
    """Even with a primary_source, if the resolver returns None the
    safe fallback applies and is escape-clean."""
    eng = _engine_with_resolved(monkeypatch, duck_engine_module, latest=None)
    out = eng._inject_latest_date(
        "WHERE load_date = '{latest_date}'", sources=["bronze.foo"],
    )
    assert out == "WHERE load_date = '1970-01-01'"


# ── Consistency with the pre-existing _sql_quote at line 245 ───────────────

def test_consistent_quote_behaviour_with_sql_quote(duck_engine_module):
    """Pre-v1.43.1, line 245 already used ``_sql_quote(str(latest['load_date']))``
    — but in that context the template didn't carry outer quotes
    (``WHERE load_date = ?``). The new helper handles the OPPOSITE
    case (template DOES carry outer quotes). Both must consistently
    double embedded single quotes — that's the SQL-standard escape."""
    raw = "a'b'c"
    via_sql_quote_inner = duck_engine_module._sql_quote(raw)
    via_inner_only      = duck_engine_module._escape_sql_literal_inner(raw)
    # Inner doubling is identical:
    assert via_sql_quote_inner == "'" + via_inner_only + "'"


def test_injection_attempt_does_not_escape_literal(monkeypatch, duck_engine_module):
    """End-to-end: a payload that would otherwise break out and DROP
    a table. After injection, the resulting SQL contains the payload
    as data, not as a statement boundary."""
    payload = "2024-01-01'; DROP TABLE secrets; --"
    eng = _engine_with_resolved(monkeypatch, duck_engine_module, latest=payload)
    out = eng._inject_latest_date(
        "SELECT * FROM bronze WHERE load_date = '{latest_date}'",
        sources=["bronze.foo"],
    )
    # The semicolon and DROP are still in the string, but they sit
    # INSIDE the single-quoted literal because the only `'` characters
    # in the payload have been doubled — the parser sees them as data.
    inside_literal = "'2024-01-01''; DROP TABLE secrets; --'"
    assert out.endswith(inside_literal)
