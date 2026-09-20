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


def test_duckdb_connection_bounds_the_spill_directory(
    engine_module, monkeypatch, tmp_path
):
    # A bounded memory_limit makes spilling normal rather than rare, so the
    # spill needs a bound of its own: DuckDB's default cap is 90% of the
    # filesystem, and on a single-disk host that starves PostgreSQL.
    spill = tmp_path / "spill"
    fake = FakeDuckDBConnection()
    monkeypatch.setenv("DUCKDB_MEMORY_LIMIT", "1GB")
    monkeypatch.setenv("DUCKDB_TEMP_DIRECTORY", str(spill))
    monkeypatch.setenv("DUCKDB_MAX_TEMP_DIRECTORY_SIZE", "8GB")
    monkeypatch.setattr(engine_module.duckdb, "__version__", "1.2.2", raising=False)
    monkeypatch.setattr(engine_module.duckdb, "connect", lambda **_kwargs: fake)

    engine = engine_module.DuckDBEngine()
    engine._conn()

    joined = "\n".join(fake.statements)
    assert f"SET temp_directory='{spill}';" in joined
    assert "SET max_temp_directory_size='8GB';" in joined
    # The directory has to exist before the cap can apply to it.
    assert joined.index("SET temp_directory=") < joined.index(
        "SET max_temp_directory_size="
    )


def test_spill_settings_are_omitted_when_unset(engine_module, monkeypatch):
    # Absent configuration must not turn into an empty SET, which DuckDB would
    # read as "spill into the current working directory".
    fake = FakeDuckDBConnection()
    monkeypatch.delenv("DUCKDB_TEMP_DIRECTORY", raising=False)
    monkeypatch.delenv("DUCKDB_MAX_TEMP_DIRECTORY_SIZE", raising=False)
    monkeypatch.setattr(engine_module.duckdb, "__version__", "1.2.2", raising=False)
    monkeypatch.setattr(engine_module.duckdb, "connect", lambda **_kwargs: fake)

    engine = engine_module.DuckDBEngine()
    engine._conn()

    joined = "\n".join(fake.statements)
    assert "temp_directory" not in joined
    assert "max_temp_directory_size" not in joined


@pytest.mark.parametrize("value", ["", "/tmp/duckdb", "/var/lib/duckdb/spill"])
def test_temp_directory_validator_accepts_absolute_paths(engine_module, value):
    assert engine_module._duckdb_temp_directory_from_env(value) == value


@pytest.mark.parametrize(
    "value",
    [
        ".tmp",                      # DuckDB's own default: relative to the cwd
        "relative/spill",
        "/var/lib/duckdb'; DROP TABLE x; --",
        "/var/lib/duckdb\nSET memory_limit='64GB'",
    ],
)
def test_temp_directory_validator_rejects_relative_and_injected_values(
    engine_module, value
):
    with pytest.raises(ValueError):
        engine_module._duckdb_temp_directory_from_env(value)


@pytest.mark.parametrize("value", ["8GB", "512MB", "2.5GiB"])
def test_max_temp_directory_size_validator_accepts_sizes(engine_module, value):
    assert engine_module._duckdb_max_temp_directory_size_from_env(value) == value


@pytest.mark.parametrize("value", ["90%", "lots", "-1GB", "8 gigabytes"])
def test_max_temp_directory_size_validator_rejects_nonsense(engine_module, value):
    with pytest.raises(ValueError):
        engine_module._duckdb_max_temp_directory_size_from_env(value)


def test_spill_directory_is_created_before_use(engine_module, monkeypatch, tmp_path):
    # DuckDB creates one missing directory but not a nested path: with the
    # parent absent it raises "Failed to create directory" at spill time, so
    # the query that needed to spill is the one that fails.
    target = tmp_path / "deep" / "nested" / "spill"
    fake = FakeDuckDBConnection()
    monkeypatch.setenv("DUCKDB_TEMP_DIRECTORY", str(target))
    monkeypatch.setattr(engine_module.duckdb, "__version__", "1.2.2", raising=False)
    monkeypatch.setattr(engine_module.duckdb, "connect", lambda **_kwargs: fake)

    engine = engine_module.DuckDBEngine()
    engine._conn()

    assert target.is_dir()
    assert f"SET temp_directory='{target}';" in "\n".join(fake.statements)


def test_an_uncreatable_spill_directory_fails_loudly(engine_module, monkeypatch, tmp_path):
    # Silently falling back to the default would reinstate the 90%-of-disk cap
    # that the setting exists to replace.
    blocker = tmp_path / "a-file"
    blocker.write_text("not a directory", encoding="utf-8")
    fake = FakeDuckDBConnection()
    monkeypatch.setenv("DUCKDB_TEMP_DIRECTORY", str(blocker / "spill"))
    monkeypatch.setattr(engine_module.duckdb, "__version__", "1.2.2", raising=False)
    monkeypatch.setattr(engine_module.duckdb, "connect", lambda **_kwargs: fake)

    engine = engine_module.DuckDBEngine()
    with pytest.raises(RuntimeError, match="DUCKDB_TEMP_DIRECTORY"):
        engine._conn()
