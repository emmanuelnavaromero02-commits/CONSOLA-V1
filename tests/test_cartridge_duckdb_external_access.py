from __future__ import annotations

import ast
import importlib
import re
import sys
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
