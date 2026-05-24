"""P4 — _inject_latest_date must fail loud when the latest-date resolver is
not scope-aware, instead of silently degrading tenant isolation.

History: when user_context (tenant_id/workspace_id) was threaded through the
DuckDB engine for multi-tenancy, _inject_latest_date wrapped the resolver call
in ``try: resolver(src, ctx) except TypeError: resolver(src)``. That fallback
existed only so pre-scope test doubles kept working — but it meant any caller
stuck on the old one-argument signature would resolve the latest load_date
across EVERY tenant without raising. These tests pin the fail-loud behavior:
the scope is always passed through, and an old-signature resolver now raises.
"""
import os
import sys
from unittest.mock import MagicMock

import pytest

sys.modules.setdefault("duckdb", MagicMock())
sys.modules.setdefault("psycopg2", MagicMock())
os.environ.setdefault("MINIO_SECRET_KEY", "test")
os.environ.setdefault("MINIO_ACCESS_KEY", "test")
os.environ.setdefault("MINIO_ENDPOINT", "test")

from refinement.app.duckdb_engine import DuckDBEngine

SQL = "SELECT * FROM bronze WHERE load_date = '{latest_date}'"
SOURCES = ["raw/replicon/timesheets"]


def _engine():
    # _inject_latest_date only needs _resolve_latest_date + string ops, so we
    # bypass __init__ (which wants real MinIO/Postgres config).
    return DuckDBEngine.__new__(DuckDBEngine)


def test_inject_latest_date_threads_user_context_to_resolver():
    """Normal path: the scope-aware resolver receives user_context and its
    result is substituted into the SQL."""
    eng = _engine()
    captured = {}

    def resolver(source, user_context=None):
        captured["source"] = source
        captured["ctx"] = user_context
        return "2026-05-01"

    eng._resolve_latest_date = resolver
    ctx = {"tenant_id": "t-1", "workspace_id": "w-1"}
    out = eng._inject_latest_date(SQL, SOURCES, ctx)

    assert "2026-05-01" in out
    assert "{latest_date}" not in out
    # The tenant/workspace scope must reach the resolver — that is the whole
    # point of threading user_context instead of resolving load_date globally.
    assert captured["ctx"] == ctx
    assert captured["source"] == SOURCES[0]


def test_inject_latest_date_fails_loud_on_pre_scope_resolver():
    """Regression guard: a resolver stuck on the old one-argument signature
    must raise loudly. Before the fix, _inject_latest_date swallowed the
    TypeError and re-called the resolver WITHOUT user_context, silently
    resolving the latest load_date across every tenant."""
    eng = _engine()
    eng._resolve_latest_date = lambda source: "2026-05-01"  # pre-scope signature

    with pytest.raises(TypeError):
        eng._inject_latest_date(SQL, SOURCES, {"tenant_id": "t-1"})
