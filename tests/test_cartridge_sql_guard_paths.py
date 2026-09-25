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
        "FROM {read} a JOIN E'/etc/passwd' b ON true",
        "FROM {read} a JOIN 'x.csv' b ON true",
        "FROM (TABLE 'x.csv')",
        "FROM (SUMMARIZE 'x.csv')",
        "FROM (FROM 'x.csv')",
        "FROM {read} a WHERE a.id IN (FROM '/x.csv')",
        "FROM {read} a, \"x.csv\" b",
        "FROM {read} a, LATERAL (FROM '/etc/hosts') b",
        "FROM main.\"read_text\"(chr(47) || 'etc' || chr(47) || 'hosts')",
        "FROM \"glob\"('*')",
        "FROM main.\"read_csv\"(chr(47) || 'proc', header=false)",
        "FROM main.read_text(chr(47) || 'etc')",
        "FROM {read} UNION ALL SELECT * FROM main . \"read_blob\" ('/etc/hosts')",
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


@pytest.mark.parametrize("cartridge", CARTRIDGES)
@pytest.mark.parametrize(
    "tail",
    [
        "SELECT replace(CardName, '/', '-') FROM {read} LIMIT 5",
        "SELECT * FROM {read} WHERE U_Web LIKE 'https://%' LIMIT 5",
        "SELECT * FROM {read} WHERE FileName = 'x.csv' LIMIT 5",
        "SELECT coalesce(name, 'unknown.txt') AS n FROM {read} LIMIT 5",
        "SELECT * FROM {read} WHERE status IN ('open', 'closed') LIMIT 5",
        "SELECT strftime(DocDate, '%Y-%m') AS m FROM {read} GROUP BY 1, 'report.csv' LIMIT 5",
        "SELECT CASE WHEN a > 1 THEN '/root' ELSE 'c:/x' END FROM {read} LIMIT 5",
        "SELECT \"DocNum\", \"CardCode\" FROM {read} AS \"orders.csv\" LIMIT 5",
    ],
)
def test_path_like_values_outside_relation_positions_pass(cartridge, tail):
    prefixes = tuple(f"s3://lakehouse/{layer}/{cartridge}/" for layer in ("raw", "silver", "gold"))
    sql = tail.replace("{read}", _read(cartridge))
    assert _guard(cartridge).validate_kb_sql(sql, prefixes, required_scope=SCOPE) == (True, None)
