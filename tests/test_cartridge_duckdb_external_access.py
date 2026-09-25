from __future__ import annotations

import ast
import importlib
import os
import re
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CARTRIDGES = ("hubspot", "replicon", "salesforce", "sap_b1", "sap_hcm", "sap_s4hana", "sap_successfactors")


def _real_duckdb():
    current = sys.modules.get("duckdb")
    if current is not None and getattr(current, "__file__", None) is None:
        sys.modules.pop("duckdb")
        try:
            return importlib.import_module("duckdb")
        finally:
            sys.modules["duckdb"] = current
    return importlib.import_module("duckdb")


duckdb = _real_duckdb()


def _source(cartridge: str) -> str:
    return (ROOT / "cartridges" / cartridge / "app" / "services" / "duckdb_service.py").read_text(encoding="utf-8")


def _helper(cartridge: str):
    tree = ast.parse(_source(cartridge))
    wanted = [
        node
        for node in tree.body
        if (isinstance(node, ast.FunctionDef) and node.name == "_restrict_external_access")
        or (isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "_BUCKET_NAME_RE" for t in node.targets))
    ]
    namespace = {"re": re, "duckdb": duckdb}
    exec(compile(ast.Module(body=wanted, type_ignores=[]), cartridge, "exec"), namespace)
    return namespace["_restrict_external_access"]


@pytest.mark.parametrize("cartridge", CARTRIDGES)
def test_query_connection_is_locked_to_the_lakehouse_before_configuration_freezes(cartridge):
    source = _source(cartridge)
    restrict = source.index("    _restrict_external_access(conn, ")
    lock = source.index('    conn.execute("SET lock_configuration=true;")')
    assert restrict < lock


@pytest.mark.parametrize("cartridge", CARTRIDGES)
@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM read_text('/etc/hosts')",
        "SELECT * FROM read_csv('/etc/hosts')",
        "SELECT * FROM glob('/etc/*')",
        "SELECT * FROM read_parquet('s3://other-bucket/x.parquet')",
    ],
)
def test_locked_connection_refuses_local_files_and_other_buckets(cartridge, sql):
    conn = duckdb.connect()
    _helper(cartridge)(conn, "lakehouse-test")
    conn.execute("SET lock_configuration=true;")
    with pytest.raises(duckdb.Error):
        conn.execute(sql).fetchall()
    assert conn.execute("SELECT count(*) FROM range(5)").fetchone() == (5,)


@pytest.mark.parametrize("cartridge", CARTRIDGES)
@pytest.mark.parametrize("bucket", ["", None, "a", "x'];SET enable_external_access=true;--", "Bucket"])
def test_unusable_bucket_names_are_refused(cartridge, bucket):
    with pytest.raises(RuntimeError, match="storage_access_denied"):
        _helper(cartridge)(duckdb.connect(), bucket)


_RESOURCE_NAMES = {
    "_DUCKDB_SIZE_UNITS",
    "_DUCKDB_SIZE_RE",
    "_CGROUP_MEMORY_LIMIT_FILES",
    "_DUCKDB_FALLBACK_MEMORY_LIMIT",
    "_duckdb_size_bytes",
    "_container_memory_limit_bytes",
    "_duckdb_memory_limit",
    "_apply_resource_limits",
}


def _resource_nodes(cartridge: str) -> list[ast.stmt]:
    return [
        node
        for node in ast.parse(_source(cartridge)).body
        if (isinstance(node, ast.FunctionDef) and node.name in _RESOURCE_NAMES)
        or (
            isinstance(node, ast.Assign)
            and any(getattr(t, "id", "") in _RESOURCE_NAMES for t in node.targets)
        )
    ]


def _resource_helpers(cartridge: str, cgroup_files: tuple[str, ...] = ()) -> dict:
    namespace = {"re": re, "os": os, "Path": Path, "duckdb": duckdb}
    module = ast.Module(body=_resource_nodes(cartridge), type_ignores=[])
    exec(compile(module, cartridge, "exec"), namespace)
    namespace["_CGROUP_MEMORY_LIMIT_FILES"] = cgroup_files
    return namespace


def test_resource_limit_helpers_are_identical_across_cartridges():
    sources = {
        cartridge: [ast.unparse(node) for node in _resource_nodes(cartridge)]
        for cartridge in CARTRIDGES
    }
    assert len(sources["sap_b1"]) == len(_RESOURCE_NAMES)
    assert all(nodes == sources["sap_b1"] for nodes in sources.values())


@pytest.mark.parametrize("cartridge", CARTRIDGES)
def test_resource_limits_are_set_before_the_lockdown(cartridge):
    source = _source(cartridge)
    limits = source.index("    _apply_resource_limits(conn)\n")
    restrict = source.index("    _restrict_external_access(conn, ")
    lock = source.index('    conn.execute("SET lock_configuration=true;")')
    assert limits < restrict < lock


@pytest.mark.parametrize(
    ("configured", "cgroup", "expected"),
    [
        ("", None, "1GB"),
        ("4GB", None, "4GB"),
        ("4GB", "3221225472", "2150MiB"),
        ("512MB", "3221225472", "512MB"),
        ("", "max", "1GB"),
    ],
)
def test_query_memory_is_the_smaller_of_env_and_seventy_percent_of_the_container(
    monkeypatch, tmp_path, configured, cgroup, expected
):
    files = ()
    if cgroup is not None:
        (tmp_path / "memory.max").write_text(cgroup, encoding="ascii")
        files = (str(tmp_path / "memory.max"),)
    monkeypatch.setenv("DUCKDB_MEMORY_LIMIT", configured)

    assert _resource_helpers("sap_b1", files)["_duckdb_memory_limit"]() == expected


def test_invalid_resource_configuration_closes_the_connection(monkeypatch):
    monkeypatch.setenv("DUCKDB_MEMORY_LIMIT", "lots")
    conn = duckdb.connect()

    with pytest.raises(RuntimeError, match="duckdb_resource_limits_invalid"):
        _resource_helpers("sap_b1")["_apply_resource_limits"](conn)
    with pytest.raises(duckdb.Error):
        conn.execute("SELECT 1")


def test_each_query_connection_gets_a_private_spill_directory(monkeypatch, tmp_path):
    monkeypatch.setenv("DUCKDB_TEMP_DIRECTORY", str(tmp_path / "spill"))
    helpers = _resource_helpers("sap_b1")
    first, second = duckdb.connect(), duckdb.connect()
    helpers["_apply_resource_limits"](first)
    helpers["_apply_resource_limits"](second)
    directories = [
        Path(conn.execute("SELECT current_setting('temp_directory')").fetchone()[0])
        for conn in (first, second)
    ]

    assert directories[0] != directories[1]
    assert {directory.parent for directory in directories} == {tmp_path / "spill"}


def test_locked_query_connection_spills_without_exposing_its_temp_directory(
    monkeypatch, tmp_path
):
    spill = tmp_path / "spill"
    monkeypatch.setenv("DUCKDB_MEMORY_LIMIT", "32MB")
    monkeypatch.setenv("DUCKDB_TEMP_DIRECTORY", str(spill))
    conn = duckdb.connect()
    conn.execute("SET threads=2;")
    _resource_helpers("sap_b1")["_apply_resource_limits"](conn)
    _helper("sap_b1")(conn, "lakehouse-test")
    conn.execute("SET lock_configuration=true;")
    seen = [0]
    done = threading.Event()

    def watch():
        while not done.is_set():
            seen[0] = max(seen[0], sum(len(files) for _, _, files in os.walk(spill)))
            time.sleep(0.002)

    watcher = threading.Thread(target=watch)
    watcher.start()
    try:
        result = conn.execute(
            "SELECT count(*), sum(x) FROM (SELECT i AS x, md5(i::VARCHAR) || "
            "md5((i * 7)::VARCHAR) AS s FROM range(1000000) r(i) ORDER BY s)"
        ).fetchone()
    finally:
        done.set()
        watcher.join()

    assert result == (1_000_000, 499_999_500_000)
    assert seen[0] > 0
    assert conn.execute("SELECT current_setting('preserve_insertion_order')").fetchone() == (False,)
    with pytest.raises(duckdb.Error):
        conn.execute(f"SELECT * FROM glob('{spill}/*')").fetchall()
    with pytest.raises(duckdb.Error):
        conn.execute("SET memory_limit='8GB';")
    with pytest.raises(duckdb.Error):
        conn.execute("SET temp_directory='/tmp';")
    conn.close()
    assert list(spill.iterdir()) == []
