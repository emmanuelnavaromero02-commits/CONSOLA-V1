from __future__ import annotations

import importlib
import importlib.util
import os
import re
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "mcp-infra" / "app" / "duckdb_runtime.py"
GIB = 1024**3
MIB = 1024**2
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


@pytest.fixture()
def runtime():
    spec = importlib.util.spec_from_file_location("mcp_infra_duckdb_runtime", RUNTIME)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.duckdb = _real_duckdb()
    return module


class FakeConnection:
    def __init__(self) -> None:
        self.statements: list[str] = []
        self.closed = False

    def execute(self, sql: str):
        self.statements.append(sql)
        return self

    def fetchall(self):
        return [("httpfs",)]

    def close(self) -> None:
        self.closed = True


def _fake_duckdb(connection: FakeConnection):
    return SimpleNamespace(__version__="1.2.2", connect=lambda: connection)


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        ({"memory.max": "1610612736\n"}, 1536 * MIB),
        ({"memory.max": "max\n"}, None),
        ({"memory.max": "garbage\n"}, None),
        ({"memory.limit_in_bytes": "2147483648\n"}, 2 * GIB),
        ({"memory.limit_in_bytes": "9223372036854771712\n"}, None),
        ({}, None),
    ],
)
def test_container_memory_limit_reads_cgroup_v2_then_v1(runtime, tmp_path, files, expected):
    for name, value in files.items():
        (tmp_path / name).write_text(value, encoding="ascii")
    paths = (str(tmp_path / "memory.max"), str(tmp_path / "memory.limit_in_bytes"))

    assert runtime.container_memory_limit_bytes(paths) == expected


@pytest.mark.parametrize(
    ("configured", "container", "expected"),
    [
        ("", 1536 * MIB, "1075MiB"),
        ("512MB", 1536 * MIB, "512MB"),
        ("4GB", 1536 * MIB, "1075MiB"),
        ("4GB", None, "4GB"),
        ("", None, "1GB"),
    ],
)
def test_memory_limit_is_the_smaller_known_bound(runtime, configured, container, expected):
    assert runtime.effective_memory_limit(configured, container) == expected


def test_runtime_connection_is_bounded_after_extensions_load(runtime, monkeypatch, tmp_path):
    fake = FakeConnection()
    monkeypatch.setattr(runtime, "duckdb", _fake_duckdb(fake))
    monkeypatch.setattr(runtime, "container_memory_limit_bytes", lambda: 1536 * MIB)
    monkeypatch.delenv("DUCKDB_MEMORY_LIMIT", raising=False)
    monkeypatch.setenv("DUCKDB_TEMP_DIRECTORY", str(tmp_path / "spill"))

    assert runtime.connect_duckdb_runtime() is fake

    statements = fake.statements
    assert statements.index("LOAD httpfs") < statements.index(
        "SET preserve_insertion_order=false"
    )
    assert "SET memory_limit='1075MiB'" in statements
    spill = [s for s in statements if s.startswith("SET temp_directory=")]
    assert len(spill) == 1
    assert re.fullmatch(
        rf"SET temp_directory='{re.escape(str(tmp_path / 'spill'))}/[0-9a-f]{{32}}'",
        spill[0],
    )
    assert not fake.closed


@pytest.mark.parametrize(
    ("variable", "value"),
    [("DUCKDB_MEMORY_LIMIT", "lots"), ("DUCKDB_TEMP_DIRECTORY", "relative/spill")],
)
def test_invalid_resource_configuration_closes_the_connection(
    runtime, monkeypatch, variable, value
):
    fake = FakeConnection()
    monkeypatch.setattr(runtime, "duckdb", _fake_duckdb(fake))
    monkeypatch.setenv(variable, value)

    with pytest.raises(RuntimeError, match="resource limits are invalid"):
        runtime.connect_duckdb_runtime()
    assert fake.closed


def test_bounded_connection_spills_to_its_private_folder(runtime, monkeypatch, tmp_path):
    spill = tmp_path / "spill"
    monkeypatch.setenv("DUCKDB_MEMORY_LIMIT", "32MB")
    monkeypatch.setenv("DUCKDB_TEMP_DIRECTORY", str(spill))
    duckdb = _real_duckdb()
    first, second = duckdb.connect(), duckdb.connect()
    for connection in (first, second):
        connection.execute("SET threads=2")
        runtime.apply_resource_limits(connection)
    folders = {
        Path(connection.execute("SELECT current_setting('temp_directory')").fetchone()[0])
        for connection in (first, second)
    }
    assert len(folders) == 2 and {folder.parent for folder in folders} == {spill}
    assert first.execute("SELECT current_setting('preserve_insertion_order')").fetchone() == (False,)

    seen = [0]
    done = threading.Event()

    def watch():
        while not done.is_set():
            seen[0] = max(seen[0], sum(len(files) for _, _, files in os.walk(spill)))
            time.sleep(0.002)

    watcher = threading.Thread(target=watch)
    watcher.start()
    try:
        result = first.execute(HEAVY_SORT).fetchone()
    finally:
        done.set()
        watcher.join()

    assert result == (1_000_000, 499_999_500_000)
    assert seen[0] > 0
    first.close()
    second.close()
    assert list(spill.iterdir()) == []


def test_every_mcp_infra_duckdb_connection_goes_through_the_bounded_runtime():
    offenders = [
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "mcp-infra" / "app").rglob("*.py")
        if path != RUNTIME and "duckdb.connect(" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []
    runtime_source = RUNTIME.read_text(encoding="utf-8")
    assert runtime_source.index("require_loaded_extensions(connection)") < runtime_source.index(
        "apply_resource_limits(connection)"
    )
