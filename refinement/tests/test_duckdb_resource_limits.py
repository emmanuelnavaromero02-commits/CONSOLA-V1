from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[2]


@pytest.fixture()
def engine_module(monkeypatch):
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    monkeypatch.syspath_prepend(str(REPO / "refinement"))
    return importlib.import_module("app.duckdb_engine")


class FakeDuckDBConnection:
    def __init__(self):
        self.statements: list[str] = []

    def execute(self, sql: str, *_args, **_kwargs):
        self.statements.append(sql)
        return self


def test_duckdb_connection_applies_memory_and_thread_limits(engine_module, monkeypatch):
    fake = FakeDuckDBConnection()
    monkeypatch.setenv("DUCKDB_MEMORY_LIMIT", "256MB")
    monkeypatch.setenv("DUCKDB_THREADS", "2")
    monkeypatch.setattr(engine_module.duckdb, "__version__", "1.2.2", raising=False)
    monkeypatch.setattr(engine_module.duckdb, "connect", lambda **_kwargs: fake)

    engine = engine_module.DuckDBEngine()
    engine._conn()

    joined = "\n".join(fake.statements)
    assert "SET memory_limit='256MB';" in joined
    assert "SET threads=2;" in joined
    assert "INSTALL " not in joined
    assert joined.index("LOAD httpfs;") < joined.index("SET memory_limit='256MB';")


@pytest.mark.parametrize("value", ["", "512MB", "1GB", "1024MiB", "1.5GB"])
def test_duckdb_memory_limit_validator_accepts_safe_values(engine_module, value):
    assert engine_module._duckdb_memory_limit_from_env(value) == value


@pytest.mark.parametrize("value", ["1; DROP", "../tmp", "512", "512 elephants"])
def test_duckdb_memory_limit_validator_rejects_unsafe_values(engine_module, value):
    with pytest.raises(ValueError, match="DUCKDB_MEMORY_LIMIT"):
        engine_module._duckdb_memory_limit_from_env(value)


@pytest.mark.parametrize("value, expected", [("", None), ("1", 1), ("8", 8)])
def test_duckdb_threads_validator_accepts_bounded_ints(engine_module, value, expected):
    assert engine_module._duckdb_threads_from_env(value) == expected


@pytest.mark.parametrize("value", ["0", "-1", "65", "abc"])
def test_duckdb_threads_validator_rejects_invalid_values(engine_module, value):
    with pytest.raises(ValueError, match="DUCKDB_THREADS"):
        engine_module._duckdb_threads_from_env(value)
