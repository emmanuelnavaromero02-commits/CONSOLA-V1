"""Sprint v1.16 — preview_sql wall-clock cap.

Three layers of coverage:
  1. Constant pinned at 30s (kept as a single point of policy).
  2. Friendly error normalization — an interrupt-shaped exception in
     ``con.execute`` becomes a "Query exceeded timeout …" message, not a
     stack trace fragment.
  3. End-to-end against a real DuckDBEngine: tighten the cap to 100 ms
     and run an in-memory generate-series scan large enough that
     ``con.interrupt()`` has to fire. Verifies the watchdog actually
     fires and the Timer.cancel() in the success path doesn't leak.

The end-to-end test installs a real duckdb connection but never touches
S3 / Postgres, so it works in CI as long as the `duckdb` wheel is
available.
"""
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
    """Load the real duckdb_engine module. duckdb must be importable —
    refinement's runtime depends on it anyway. psycopg2 is stubbed
    because the module imports it at load time even though the timeout
    path under test never touches Postgres."""
    # Several peer tests in this directory install MagicMock() as
    # sys.modules['duckdb'] at IMPORT time (test_rls.py, test_rls_hardening.py,
    # test_preview_sql_limit_cap.py) and never restore it. If a peer ran
    # first and we just `import duckdb`, we get the mock and the real
    # InterruptException class is unreachable. Force a fresh import.
    from unittest.mock import MagicMock as _MagicMock
    existing = sys.modules.get("duckdb")
    if existing is None or isinstance(existing, _MagicMock):
        sys.modules.pop("duckdb", None)
    # Also pop our module so it picks up the fresh `duckdb` symbol.
    sys.modules.pop("app.duckdb_engine", None)
    try:
        import duckdb  # noqa: F401
        # Sanity: if a peer also stubbed `duckdb.connect` as a MagicMock,
        # connect() returns a Mock instead of a real connection. Detect.
        if not callable(getattr(duckdb, "connect", None)) or isinstance(
            getattr(duckdb, "connect", None), _MagicMock
        ):
            pytest.skip("duckdb is stubbed by a peer test; cannot drive a real connection")
    except ImportError:
        pytest.skip("duckdb wheel not installed in this env")
    # psycopg2 is unavailable in this test environment (same gap as the
    # pre-existing refinement vault tests document). The timeout path
    # never calls it, so a minimal stub is sufficient.
    if "psycopg2" not in sys.modules:
        monkeypatch.setitem(sys.modules, "psycopg2", _module(connect=lambda *a, **kw: None))
    return importlib.import_module("app.duckdb_engine")


# ── Policy constant ─────────────────────────────────────────────────


def test_statement_timeout_constant_is_thirty_seconds(engine_module):
    """The cap is exposed as a class-level constant so it can be tuned
    in one place (and tested without instantiating the engine)."""
    eng_cls = engine_module.DuckDBEngine
    assert hasattr(eng_cls, "_STATEMENT_TIMEOUT_SECONDS"), (
        "DuckDBEngine must declare _STATEMENT_TIMEOUT_SECONDS"
    )
    assert eng_cls._STATEMENT_TIMEOUT_SECONDS == 30


# ── Error normalization ─────────────────────────────────────────────


class _FakeCursor:
    description: list = []

    def fetchall(self):
        return []


class _RaisingConn:
    """Pretends to be a DuckDB connection. Raises a configurable exception
    on ``execute`` so we can prove the error-shape detector works without
    actually running a slow query."""

    def __init__(self, exc):
        self._exc = exc

    def execute(self, *_args, **_kwargs):
        raise self._exc

    def interrupt(self):  # called by the watchdog; harmless in this mock
        pass


def _wire_minimal_engine(engine_module, conn):
    """Build a DuckDBEngine instance with the heavy dependencies short-circuited
    so we can drive preview_sql with a mock connection."""
    eng = engine_module.DuckDBEngine.__new__(engine_module.DuckDBEngine)
    import threading as _t
    eng._duckdb_lock = _t.RLock()
    eng._con = conn  # so _conn() returns this directly
    # Bypass safety policy, RLS and bucket interpolation — none of them
    # are under test here; we only want to reach the execute() call.
    eng._validate_safe_sql = lambda sql: None
    eng.get_rls_filters = lambda sql, user_ctx: (sql, [])
    eng._inject_bucket = lambda sql: sql
    eng._inject_latest_date = lambda sql, srcs: sql
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
        # Non-timeout errors are echoed verbatim so the caller can
        # actually see the parser/RLS message.
        assert "exceeded timeout" not in err, err


def test_preview_sql_detects_duckdb_interrupt_exception_class(engine_module):
    """If the real duckdb wheel raises its dedicated InterruptException
    (no 'interrupt' substring guaranteed in str()), we still classify it
    as a timeout by class name."""
    try:
        import duckdb
        InterruptExc = duckdb.InterruptException
    except (ImportError, AttributeError):
        pytest.skip("duckdb.InterruptException not available")

    eng = _wire_minimal_engine(engine_module, _RaisingConn(InterruptExc("anything")))
    result = eng.preview_sql("SELECT 1", limit=10)
    assert "error" in result
    assert "exceeded timeout" in result["error"]


# ── End-to-end with a real DuckDB connection ────────────────────────


def test_preview_sql_actually_interrupts_long_running_query(engine_module, monkeypatch):
    """Tightens the cap to 100 ms, then runs an in-memory query that
    DuckDB cannot finish in that window. Asserts the watchdog fired and
    the friendly error was returned. Subsequent calls on the same
    connection must still work — interrupt() must not poison it."""
    import duckdb

    eng = engine_module.DuckDBEngine.__new__(engine_module.DuckDBEngine)
    import threading as _t
    eng._duckdb_lock = _t.RLock()
    real_con = duckdb.connect()
    eng._con = real_con
    eng._validate_safe_sql = lambda sql: None
    eng.get_rls_filters = lambda sql, user_ctx: (sql, [])
    eng._inject_bucket = lambda sql: sql
    eng._inject_latest_date = lambda sql, srcs: sql
    eng._conn = lambda: real_con

    # Tighten the cap so the test runs in <1s. We do this on the
    # instance so the class default stays 30 for everyone else.
    eng._STATEMENT_TIMEOUT_SECONDS = 0.2

    # range(N) with a large N forces DuckDB into a long enumeration.
    # 50_000_000_000 takes seconds even on fast hardware.
    result = eng.preview_sql(
        "SELECT count(*) FROM range(50000000000)", limit=10,
    )
    assert "error" in result, result
    assert "exceeded timeout" in result["error"], result

    # The connection must still be usable after an interrupt — otherwise
    # the engine is permanently broken after the first runaway query.
    healthy = eng.preview_sql("SELECT 1 AS one", limit=10)
    assert "data" in healthy, healthy
    assert healthy["row_count"] == 1
