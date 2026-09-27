from __future__ import annotations

import importlib
import sys
import threading
import types

import pytest


@pytest.fixture()
def engine_module(monkeypatch):
    from unittest.mock import MagicMock

    if isinstance(sys.modules.get("duckdb"), MagicMock):
        monkeypatch.delitem(sys.modules, "duckdb")
    sys.modules.pop("app.duckdb_engine", None)
    return importlib.import_module("app.duckdb_engine")


class _SlowConn:
    def __init__(self):
        self.interrupted = threading.Event()

    def execute(self, sql, *args):
        if not self.interrupted.wait(5):
            raise AssertionError("describe was never interrupted")
        raise RuntimeError("INTERRUPT Error: Interrupted!")

    def interrupt(self):
        self.interrupted.set()


class _FastConn:
    def __init__(self):
        self.interrupted = False
        self.sql = ""

    def execute(self, sql, *args):
        self.sql = sql
        return types.SimpleNamespace(fetchall=lambda: [("id", "VARCHAR")])

    def interrupt(self):
        self.interrupted = True


def _engine(engine_module, conn):
    engine = engine_module.DuckDBEngine.__new__(engine_module.DuckDBEngine)
    engine._duckdb_lock = threading.RLock()
    engine._conn = lambda: conn
    engine._bronze_read = lambda source, ctx: "read_parquet('s3://lakehouse/raw/acme/E/**/*.parquet')"
    return engine


def test_schema_describe_is_interrupted_after_the_timeout(engine_module):
    conn = _SlowConn()
    result = _engine(engine_module, conn).get_source_schema("raw/acme/E", {}, timeout_seconds=0.05)
    assert conn.interrupted.is_set()
    assert "INTERRUPT" in result["error"]


def test_schema_describe_without_timeout_never_interrupts(engine_module):
    conn = _FastConn()
    result = _engine(engine_module, conn).get_source_schema("raw/acme/E", {})
    assert result["fields"] == [{"name": "id", "type": "VARCHAR"}]
    assert conn.sql.startswith("DESCRIBE SELECT * FROM read_parquet(")
    assert conn.interrupted is False


def test_fast_schema_describe_cancels_its_timer(engine_module):
    conn = _FastConn()
    result = _engine(engine_module, conn).get_source_schema("raw/acme/E", {}, timeout_seconds=0.05)
    threading.Event().wait(0.15)
    assert result["fields"] == [{"name": "id", "type": "VARCHAR"}]
    assert conn.interrupted is False
