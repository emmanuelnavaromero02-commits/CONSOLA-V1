from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
CARTRIDGES = ("hubspot", "replicon", "salesforce", "sap_b1", "sap_hcm", "sap_s4hana", "sap_successfactors")
SCOPE = "tenant_id=t1/workspace_id=w1"


def _guard(cartridge: str):
    path = REPO / "cartridges" / cartridge / "app" / "core" / "sql_guard.py"
    spec = importlib.util.spec_from_file_location(f"sql_guard_{cartridge}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _read(cartridge: str) -> str:
    return f"read_parquet('s3://lakehouse/raw/{cartridge}/X/{SCOPE}/**/*.parquet')"


def test_every_cartridge_ships_the_same_guard():
    sources = {(REPO / "cartridges" / c / "app" / "core" / "sql_guard.py").read_bytes() for c in CARTRIDGES}
    assert len(sources) == 1


@pytest.mark.parametrize("cartridge", CARTRIDGES)
@pytest.mark.parametrize(
    "tail",
    [
        "FROM 's3://lakehouse/raw/other/X/tenant_id=t2/workspace_id=w2/a.parquet'",
        "FROM '/etc/passwd'",
        "FROM E'/tmp/secret.csv'",
        'FROM "secret.csv"',
        "FROM glob('/etc/*')",
        "FROM sniff_csv('/etc/hosts')",
        "FROM parquet_metadata('s3://lakehouse/raw/other/a.parquet')",
        "FROM {read} a, '/tmp/secret.csv' b",
        "FROM {read} a, 'file:///etc/passwd' b",
    ],
)
def test_path_literals_outside_a_validated_reader_are_blocked(cartridge, tail):
    prefixes = tuple(f"s3://lakehouse/{layer}/{cartridge}/" for layer in ("raw", "silver", "gold"))
    sql = "SELECT * " + tail.replace("{read}", _read(cartridge)) + " LIMIT 5"
    ok, reason = _guard(cartridge).validate_kb_sql(sql, prefixes, required_scope=SCOPE)
    assert ok is False, reason


@pytest.mark.parametrize("cartridge", CARTRIDGES)
def test_scoped_reads_with_ordinary_values_still_pass(cartridge):
    prefixes = tuple(f"s3://lakehouse/{layer}/{cartridge}/" for layer in ("raw", "silver", "gold"))
    sql = f"SELECT * FROM {_read(cartridge)} WHERE status = 'N/A' AND name = 'Acme S.A.' LIMIT 5"
    assert _guard(cartridge).validate_kb_sql(sql, prefixes, required_scope=SCOPE) == (True, None)
