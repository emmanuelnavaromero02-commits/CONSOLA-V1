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
    return DuckDBEngine.__new__(DuckDBEngine)


def test_inject_latest_date_threads_user_context_to_resolver():
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
    assert captured["ctx"] == ctx
    assert captured["source"] == SOURCES[0]


def test_inject_latest_date_fails_loud_on_pre_scope_resolver():
    eng = _engine()
    eng._resolve_latest_date = lambda source: "2026-05-01"

    with pytest.raises(TypeError):
        eng._inject_latest_date(SQL, SOURCES, {"tenant_id": "t-1"})
