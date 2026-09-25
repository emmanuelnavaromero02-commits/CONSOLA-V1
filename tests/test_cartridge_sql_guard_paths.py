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
        "FROM $$s3://lakehouse/raw/salesforce/Account/tenant_id=t9/workspace_id=w9/a.parquet$$",
        "FROM {read} a, $q$s3://lakehouse/raw/other/X/tenant_id=t2/workspace_id=w2/a.parquet$q$ b",
        "FROM {read} a JOIN $$/etc/hosts$$ b ON true",
        "FROM E'\\x73\\x33://lakehouse/raw/other/X/tenant_id=t2/workspace_id=w2/a.parquet'",
        "FROM {read} a, E'\\x2fetc\\x2fhosts' b",
        "FROM read_parquet(['s3://lakehouse/raw/{cartridge}/X/{scope}/a.parquet', 's3://lakehouse/raw/{cartridge}/X/tenant_id=t2/workspace_id=w2/b.parquet'])",
        "FROM read_parquet('s3://lakehouse/raw/{cartridge}/X/{scope}/a.parquet', filename = (SELECT 1))",
        "FROM {read} a WHERE a.x IS DISTINCT FROM (FROM '/etc/hosts')",
        "FROM (DESCRIBE SELECT 1)",
        "FROM range(3) r, query('SELECT 1') q",
        "FROM {read} WHERE getenv('HOME') IS NOT NULL",
        "FROM {read} WHERE current_setting('s3_secret_access_key') IS NOT NULL",
        "FROM \"/etc/hosts\" WHERE 1 IN (WITH \"/etc/hosts\" AS (SELECT 1 AS a) SELECT a FROM \"/etc/hosts\")",
        "FROM \"s3://lakehouse/raw/salesforce/A/tenant_id=t9/workspace_id=w9/a.parquet\" "
        "WHERE 1 IN (WITH \"s3://lakehouse/raw/salesforce/A/tenant_id=t9/workspace_id=w9/a.parquet\" AS (SELECT 1 AS a) "
        "SELECT a FROM \"s3://lakehouse/raw/salesforce/A/tenant_id=t9/workspace_id=w9/a.parquet\")",
        "FROM \"s3://lakehouse/raw/{cartridge}/A/tenant_id=t2/workspace_id=w2/a.parquet\" "
        "WHERE 1 IN (WITH \"s3://lakehouse/raw/{cartridge}/A/tenant_id=t2/workspace_id=w2/a.parquet\" AS (SELECT 1 AS a) "
        "SELECT a FROM \"s3://lakehouse/raw/{cartridge}/A/tenant_id=t2/workspace_id=w2/a.parquet\")",
        "FROM replicon_base_currency",
        "FROM read_parquet('s3://lakehouse/raw/{cartridge}/X/tenant_id=*/workspace_id=*/a.parquet?s3_region=x/{scope}')",
        "FROM read_parquet('s3://lakehouse/raw/{cartridge}/X/tenant_id=t2/workspace_id=w2/a.parquet%3Fs3_region=x/{scope}')",
        "FROM read_parquet('s3://lakehouse/raw/{cartridge}/X/{scope}/a.parquet#frag')",
        "FROM (WITH a AS (WITH pg_settings AS (SELECT 1 AS x) SELECT x FROM pg_settings) SELECT name, setting FROM pg_settings)",
        "FROM (WITH tables AS (SELECT 1 AS x) SELECT * FROM tables)",
        "FROM {read} WHERE json_serialize_plan('SELECT 1') IS NOT NULL",
        "FROM {read} WHERE getvariable('x') IS NOT NULL",
        "FROM {read} a, (SELECT count(*) OVER () FROM duckdb_settings()) s",
    ],
)
def test_path_literals_outside_a_validated_reader_are_blocked(cartridge, tail):
    prefixes = tuple(f"s3://lakehouse/{layer}/{cartridge}/" for layer in ("raw", "silver", "gold"))
    tail = tail.replace("{read}", _read(cartridge)).replace("{cartridge}", cartridge).replace("{scope}", SCOPE)
    sql = "SELECT * " + tail + " LIMIT 5"
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
        "SELECT * FROM {read} WHERE \"U_Web\" IS DISTINCT FROM 'https://example.com' LIMIT 5",
        "SELECT * FROM {read} WHERE \"Ref2\" IS NOT DISTINCT FROM '/' LIMIT 5",
        "SELECT * FROM {read} WHERE \"Comments\" ILIKE '%query (%' LIMIT 5",
        "WITH query(doc) AS (SELECT \"DocEntry\" FROM {read}) SELECT * FROM query LIMIT 5",
        "SELECT * FROM {read} WHERE \"ItemCode\" GLOB 'A*' LIMIT 5",
        "SELECT * FROM {read} AS \"t\"(a, b) LIMIT 5",
        "WITH \"latest\"(doc) AS (SELECT \"DocEntry\" FROM {read}) SELECT * FROM \"latest\" LIMIT 5",
        "SELECT EXTRACT(YEAR FROM \"DocDate\") AS y, trim(BOTH '/' FROM \"Ref2\") FROM {read} LIMIT 5",
        "SELECT * FROM {read} WHERE regexp_matches(\"CardCode\", '(C|P)[0-9]+') AND \"Phone1\" LIKE '(55)%' LIMIT 5",
        "SELECT price_label FROM {read} WHERE price_label = '$100' LIMIT 5",
        "SELECT * FROM read_parquet('s3://lakehouse/raw/{cartridge}/X/{scope}/**/*.parquet', hive_partitioning = true, union_by_name = true) LIMIT 5",
        "SELECT * FROM {read}, (VALUES (1), (2)) v(x), unnest([1, 2]) u, range(3) r LIMIT 5",
        "WITH inv AS (SELECT _company, y, DocTotal FROM {read}) SELECT * FROM inv PIVOT (sum(DocTotal) FOR y IN ('2025', '2026') GROUP BY _company) LIMIT 5",
        "SELECT count(*) FROM read_parquet('s3://lakehouse/raw/{cartridge}/X/{scope}/**/*.parquet', hive_partitioning := true) LIMIT 5",
        "SELECT * FROM read_parquet('s3://lakehouse/raw/{cartridge}/X/{scope}/**/*.parquet', hive_partitioning = true, hive_types = {'load_date': DATE}) LIMIT 5",
        "WITH parquet_rows(company) AS (SELECT _company FROM {read}) SELECT * FROM parquet_rows LIMIT 5",
        "SELECT * FROM {read} AS parquet_src(a, b) LIMIT 5",
        "WITH RECURSIVE t(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM t WHERE n < 3) SELECT * FROM t, {read} LIMIT 5;",
        "SELECT repeat('-', 3), histogram(DocTotal), version() FROM {read} LIMIT 5",
        "SELECT range(3), generate_series(1, 2) FROM {read} LIMIT 5",
    ],
)
def test_path_like_values_outside_relation_positions_pass(cartridge, tail):
    prefixes = tuple(f"s3://lakehouse/{layer}/{cartridge}/" for layer in ("raw", "silver", "gold"))
    sql = tail.replace("{read}", _read(cartridge)).replace("{cartridge}", cartridge).replace("{scope}", SCOPE)
    assert _guard(cartridge).validate_kb_sql(sql, prefixes, required_scope=SCOPE) == (True, None)


@pytest.mark.parametrize("cartridge", CARTRIDGES)
def test_runtime_tables_are_allowed_only_when_registered(cartridge):
    prefixes = tuple(f"s3://lakehouse/{layer}/{cartridge}/" for layer in ("raw", "silver", "gold"))
    sql = f"SELECT * FROM {_read(cartridge)} a JOIN replicon_base_currency b ON true LIMIT 5"
    guard = _guard(cartridge)
    assert guard.validate_kb_sql(sql, prefixes, required_scope=SCOPE)[0] is False
    assert guard.validate_kb_sql(
        sql, prefixes, required_scope=SCOPE, allowed_tables=frozenset({"replicon_base_currency"})
    ) == (True, None)
    assert guard.validate_kb_sql(sql, prefixes, required_scope=SCOPE, allowed_tables=frozenset({"a.csv"}))[0] is False


@pytest.mark.parametrize("cartridge", CARTRIDGES)
def test_deep_expressions_get_a_verdict_instead_of_a_crash(cartridge):
    prefixes = tuple(f"s3://lakehouse/{layer}/{cartridge}/" for layer in ("raw", "silver", "gold"))
    sql = "SELECT " + " || '|' || ".join(f"c{i}" for i in range(400)) + f" FROM {_read(cartridge)} LIMIT 5"
    assert _guard(cartridge).validate_kb_sql(sql, prefixes, required_scope=SCOPE) == (True, None)
