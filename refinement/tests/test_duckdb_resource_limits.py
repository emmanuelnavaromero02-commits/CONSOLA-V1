from __future__ import annotations

import importlib
import os
import sys
import threading
import time
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
    assert joined.index("SET temp_directory=") < joined.index(
        "SET max_temp_directory_size="
    )


def test_spill_settings_are_omitted_when_unset(engine_module, monkeypatch):
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
        ".tmp",
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
    blocker = tmp_path / "a-file"
    blocker.write_text("not a directory", encoding="utf-8")
    fake = FakeDuckDBConnection()
    monkeypatch.setenv("DUCKDB_TEMP_DIRECTORY", str(blocker / "spill"))
    monkeypatch.setattr(engine_module.duckdb, "__version__", "1.2.2", raising=False)
    monkeypatch.setattr(engine_module.duckdb, "connect", lambda **_kwargs: fake)

    engine = engine_module.DuckDBEngine()
    with pytest.raises(RuntimeError, match="DUCKDB_TEMP_DIRECTORY") as exc:
        engine._conn()
    assert str(blocker) not in str(exc.value)


def test_engine_emits_each_resource_setting_exactly_once(
    engine_module, monkeypatch, tmp_path
):
    fake = FakeDuckDBConnection()
    monkeypatch.setenv("DUCKDB_MEMORY_LIMIT", "256MB")
    monkeypatch.setenv("DUCKDB_THREADS", "2")
    monkeypatch.setenv("DUCKDB_TEMP_DIRECTORY", str(tmp_path / "spill"))
    monkeypatch.setenv("DUCKDB_MAX_TEMP_DIRECTORY_SIZE", "1GB")
    monkeypatch.setattr(engine_module.duckdb, "__version__", "1.2.2", raising=False)
    monkeypatch.setattr(engine_module.duckdb, "connect", lambda **_kwargs: fake)

    engine_module.DuckDBEngine()._conn()

    for prefix in (
        "SET memory_limit=",
        "SET preserve_insertion_order=",
        "SET threads=",
        "SET temp_directory=",
        "SET max_temp_directory_size=",
    ):
        assert sum(s.startswith(prefix) for s in fake.statements) == 1, prefix


GIB = 1024**3


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        ({"memory.max": "3221225472\n"}, 3 * GIB),
        ({"memory.max": "max\n"}, None),
        ({"memory.max": "garbage\n"}, None),
        ({"memory.limit_in_bytes": "2147483648\n"}, 2 * GIB),
        ({"memory.limit_in_bytes": "9223372036854771712\n"}, None),
        ({}, None),
    ],
)
def test_container_memory_limit_reads_cgroup_v2_then_v1(
    engine_module, tmp_path, files, expected
):
    for name, value in files.items():
        (tmp_path / name).write_text(value, encoding="ascii")
    paths = (str(tmp_path / "memory.max"), str(tmp_path / "memory.limit_in_bytes"))

    assert engine_module._container_memory_limit_bytes(paths) == expected


@pytest.mark.parametrize(
    ("configured", "container", "expected"),
    [
        ("1GB", 3 * GIB, "1GB"),
        ("4GB", 3 * GIB, "2150MiB"),
        ("", 3 * GIB, "2150MiB"),
        ("4GB", None, "4GB"),
        ("", None, "1GB"),
        ("2150MiB", 3 * GIB, "2150MiB"),
    ],
)
def test_effective_memory_limit_is_the_smaller_known_bound(
    engine_module, configured, container, expected
):
    assert (
        engine_module._effective_duckdb_memory_limit(configured, container) == expected
    )


def test_connection_caps_memory_to_the_container_and_drops_insertion_order(
    engine_module, monkeypatch
):
    fake = FakeDuckDBConnection()
    monkeypatch.setenv("DUCKDB_MEMORY_LIMIT", "4GB")
    monkeypatch.setattr(engine_module, "_container_memory_limit_bytes", lambda: 3 * GIB)
    monkeypatch.setattr(engine_module.duckdb, "__version__", "1.2.2", raising=False)
    monkeypatch.setattr(engine_module.duckdb, "connect", lambda **_kwargs: fake)

    engine_module.DuckDBEngine()._conn()

    assert "SET memory_limit='2150MiB';" in fake.statements
    assert "SET preserve_insertion_order=false;" in fake.statements


def test_unset_limits_still_bound_duckdb_memory(engine_module, monkeypatch):
    fake = FakeDuckDBConnection()
    monkeypatch.delenv("DUCKDB_MEMORY_LIMIT", raising=False)
    monkeypatch.setattr(engine_module, "_container_memory_limit_bytes", lambda: None)
    monkeypatch.setattr(engine_module.duckdb, "__version__", "1.2.2", raising=False)
    monkeypatch.setattr(engine_module.duckdb, "connect", lambda **_kwargs: fake)

    engine_module.DuckDBEngine()._conn()

    assert "SET memory_limit='1GB';" in fake.statements


def test_production_spills_to_the_omega_volume_by_default(engine_module, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("DUCKDB_TEMP_DIRECTORY", raising=False)

    engine = engine_module.DuckDBEngine()

    assert engine.duckdb_temp_directory == "/var/lib/omega/duckdb-spill"


HEAVY_SORT = (
    "SELECT count(*), sum(x) FROM (SELECT i AS x, "
    "md5(i::VARCHAR) || md5((i * 7)::VARCHAR) AS s FROM range(1000000) r(i) ORDER BY s)"
)


def _real_duckdb():
    existing = sys.modules.get("duckdb")
    if existing is not None and getattr(existing, "__file__", None) is None:
        sys.modules.pop("duckdb")
        try:
            return importlib.import_module("duckdb")
        finally:
            sys.modules["duckdb"] = existing
    return importlib.import_module("duckdb")


def _files_seen_while(directory, action):
    seen = [0]
    done = threading.Event()

    def watch():
        while not done.is_set():
            seen[0] = max(seen[0], sum(len(files) for _, _, files in os.walk(directory)))
            time.sleep(0.002)

    watcher = threading.Thread(target=watch)
    watcher.start()
    try:
        result = action()
    finally:
        done.set()
        watcher.join()
    return result, seen[0]


def test_engine_settings_make_a_large_sort_spill_instead_of_failing(
    engine_module, monkeypatch, tmp_path
):
    real_duckdb = _real_duckdb()
    real_connect = real_duckdb.connect
    spill = tmp_path / "spill"
    fake = FakeDuckDBConnection()
    monkeypatch.setenv("DUCKDB_MEMORY_LIMIT", "32MB")
    monkeypatch.setenv("DUCKDB_THREADS", "2")
    monkeypatch.setenv("DUCKDB_TEMP_DIRECTORY", str(spill))
    monkeypatch.setenv("DUCKDB_MAX_TEMP_DIRECTORY_SIZE", "2GB")
    monkeypatch.setattr(engine_module.duckdb, "__version__", "1.2.2", raising=False)
    monkeypatch.setattr(engine_module.duckdb, "connect", lambda **_kwargs: fake)
    engine_module.DuckDBEngine()._conn()
    resource_settings = [
        statement
        for statement in fake.statements
        if statement.startswith(
            (
                "SET memory_limit=",
                "SET preserve_insertion_order=",
                "SET threads=",
                "SET temp_directory=",
                "SET max_temp_directory_size=",
            )
        )
    ]
    assert len(resource_settings) == 5

    configured = real_connect()
    for statement in resource_settings:
        configured.execute(statement)
    result, spilled_files = _files_seen_while(
        spill, lambda: configured.execute(HEAVY_SORT).fetchone()
    )
    assert result == (1_000_000, 499_999_500_000)
    assert spilled_files > 0

    starved = real_connect()
    starved.execute("SET memory_limit='32MB';")
    starved.execute("SET threads=2;")
    starved.execute("SET temp_directory='';")
    with pytest.raises(real_duckdb.OutOfMemoryException):
        starved.execute(HEAVY_SORT).fetchone()
