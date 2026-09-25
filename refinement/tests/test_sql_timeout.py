from __future__ import annotations

import importlib
import sys
import types

import pytest


def _module(**attrs):
    mod = types.ModuleType("stub")
    for key, value in attrs.items():
        setattr(mod, key, value)
    return mod


@pytest.fixture()
def engine_module(monkeypatch):
    from unittest.mock import MagicMock as _MagicMock
    existing = sys.modules.get("duckdb")
    if existing is None or isinstance(existing, _MagicMock):
        sys.modules.pop("duckdb", None)
    sys.modules.pop("app.duckdb_engine", None)
    try:
        import duckdb  # noqa: F401
        if not callable(getattr(duckdb, "connect", None)) or isinstance(
            getattr(duckdb, "connect", None), _MagicMock
        ):
            pytest.skip("duckdb is stubbed by a peer test; cannot drive a real connection")
    except ImportError:
        pytest.skip("duckdb wheel not installed in this env")
    if "psycopg2" not in sys.modules:
        monkeypatch.setitem(sys.modules, "psycopg2", _module(connect=lambda *a, **kw: None))
    return importlib.import_module("app.duckdb_engine")


def test_statement_timeout_constant_is_thirty_seconds(engine_module):
    eng_cls = engine_module.DuckDBEngine
    assert hasattr(eng_cls, "_STATEMENT_TIMEOUT_SECONDS"), (
        "DuckDBEngine must declare _STATEMENT_TIMEOUT_SECONDS"
    )
    assert eng_cls._STATEMENT_TIMEOUT_SECONDS == 30


class _FakeCursor:
    description: list = []

    def fetchall(self):
        return []


class _RaisingConn:

    def __init__(self, exc):
        self._exc = exc

    def execute(self, *_args, **_kwargs):
        raise self._exc

    def interrupt(self):
        pass


def _wire_minimal_engine(engine_module, conn):
    eng = engine_module.DuckDBEngine.__new__(engine_module.DuckDBEngine)
    import threading as _t
    eng._duckdb_lock = _t.RLock()
    eng._con = conn
    eng._validate_safe_sql = lambda sql: None
    eng.get_rls_filters = lambda sql, user_ctx: (sql, [])
    eng._inject_bucket = lambda sql: sql
    eng._inject_latest_date = lambda sql, srcs, user_context=None: sql
    eng._conn = lambda: conn
    return eng


@pytest.mark.parametrize("raised, expect_timeout", [
    (RuntimeError("INTERRUPT Error: Interrupted!"), True),
    (RuntimeError("Query was interrupted"),         True),
    (RuntimeError("operation timeout"),             True),
    (ValueError("syntax error near ','"),           False),
])
def test_preview_sql_normalizes_timeout_shaped_errors(
    engine_module, raised, expect_timeout,
):
    eng = _wire_minimal_engine(engine_module, _RaisingConn(raised))
    result = eng.preview_sql("SELECT 1", limit=10)
    assert "error" in result, result
    err = result["error"]
    if expect_timeout:
        assert "exceeded timeout" in err, err
        assert "30s" in err, err
    else:
        assert "exceeded timeout" not in err, err


def test_preview_sql_normalizes_s3_listing_http_400(engine_module):
    raised = RuntimeError(
        "HTTP Error: HTTP GET error on "
        "'/?encoding-type=url&list-type=2&prefix=raw%2Fsap_successfactors%2FCandidate%2F' "
        "(HTTP 400) while reading s3://bucket/raw/sap_successfactors/Candidate/**/*.parquet"
    )
    eng = _wire_minimal_engine(engine_module, _RaisingConn(raised))

    result = eng.preview_sql("SELECT 1", limit=10)

    assert result["code"] == "s3_storage_list_failed"
    assert "No se pudo listar Parquet en S3" in result["error"]
    assert "raw_error" in result


def test_preview_sql_detects_duckdb_interrupt_exception_class(engine_module):
    try:
        import duckdb
        InterruptExc = duckdb.InterruptException
    except (ImportError, AttributeError):
        pytest.skip("duckdb.InterruptException not available")

    eng = _wire_minimal_engine(engine_module, _RaisingConn(InterruptExc("anything")))
    result = eng.preview_sql("SELECT 1", limit=10)
    assert "error" in result
    assert "exceeded timeout" in result["error"]


def test_preview_sql_actually_interrupts_long_running_query(engine_module, monkeypatch):
    import duckdb

    eng = engine_module.DuckDBEngine.__new__(engine_module.DuckDBEngine)
    import threading as _t
    eng._duckdb_lock = _t.RLock()
    real_con = duckdb.connect()
    eng._con = real_con
    eng._validate_safe_sql = lambda sql: None
    eng.get_rls_filters = lambda sql, user_ctx: (sql, [])
    eng._inject_bucket = lambda sql: sql
    eng._inject_latest_date = lambda sql, srcs, user_context=None: sql
    eng._conn = lambda: real_con

    eng._STATEMENT_TIMEOUT_SECONDS = 0.2

    result = eng.preview_sql(
        "SELECT count(*) FROM range(50000000000)", limit=10,
    )
    assert "error" in result, result
    assert "exceeded timeout" in result["error"], result

    healthy = eng.preview_sql("SELECT 1 AS one", limit=10)
    assert "data" in healthy, healthy
    assert healthy["row_count"] == 1
