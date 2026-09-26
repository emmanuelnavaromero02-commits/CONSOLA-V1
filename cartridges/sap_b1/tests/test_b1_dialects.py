from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from app.core import b1_dialects as d
from app.core import b1_source
from app.core.b1_source import B1ConfigurationError, build_config, parse_companies
from app.services import b1_queries as q

CARTRIDGE = Path(__file__).resolve().parents[1]
ENTITIES = CARTRIDGE / "app" / "config" / "entities.yaml"
STRATEGY_FREE_MODULES = (
    CARTRIDGE / "app" / "core" / "b1_source.py",
    CARTRIDGE / "app" / "services" / "b1_queries.py",
    CARTRIDGE / "app" / "services" / "b1_reader.py",
    CARTRIDGE / "app" / "services" / "source_counts_mapping.py",
    CARTRIDGE / "app" / "services" / "extraction_service.py",
    CARTRIDGE / "connect" / "windows-agent" / "agent.py",
)
ENGINE_SQL = re.compile(
    r"LIMIT \{|OFFSET \{|FETCH NEXT|FROM DUMMY|SYSDATETIME|LOCALTIMESTAMP|CURRENT_TIMESTAMP|CURRENT_DATE|DATEDIFF|"
    r"DATEADD|ADD_DAYS|DAYS_BETWEEN|\"dbo\"|hdbcli|pyodbc|psycopg2"
)


def _catalogue() -> list[dict]:
    return [dict(e) for e in yaml.safe_load(ENTITIES.read_text(encoding="utf-8"))["entities"]]


def _plan(entity: str) -> q.EntityPlan:
    return q.plan_from_config(next(e for e in _catalogue() if e["entity"] == entity))


def _config(dialect: str, **overrides) -> b1_source.B1Config:
    values = {
        "dialect": dialect,
        "host": "sql-host",
        "port": "",
        "user": "b1_reader",
        "password": "not;a}real",
        "database": "",
        "companies": "mx_a=SBO_A",
        "encrypt": True,
        "ssl_validate_certificate": True,
        "connect_timeout": 15,
    }
    values.update(overrides)
    return build_config(**values)


def test_the_registry_holds_exactly_hana_mssql_and_the_postgres_test_bed():
    assert d.DIALECTS == b1_source.DIALECTS == ("hana", "mssql", "postgres")
    assert isinstance(d.get_dialect("HANA "), d.HanaDialect)
    assert isinstance(d.get_dialect("mssql"), d.MssqlDialect)
    assert isinstance(d.get_dialect("postgres"), d.PostgresDialect)
    for name in d.DIALECTS:
        strategy = d.get_dialect(name)
        assert isinstance(strategy, d.B1DialectStrategy) and strategy.name == name
        assert d.get_dialect(strategy) is strategy
    for bad in ("oracle", "", None, "sqlserver"):
        with pytest.raises(ValueError, match="unknown SAP Business One dialect"):
            d.get_dialect(bad)
    assert b1_source.DEFAULT_PORTS == {"hana": 30015, "mssql": 1433, "postgres": 5432}


@pytest.mark.parametrize(
    "name,placeholder,now_sql,ping_sql",
    [
        ("hana", "?", "SELECT CURRENT_TIMESTAMP FROM DUMMY", "SELECT 1 FROM DUMMY"),
        ("mssql", "?", "SELECT SYSDATETIME()", "SELECT 1"),
        ("postgres", "%s", "SELECT LOCALTIMESTAMP(0)", "SELECT 1"),
    ],
    ids=["hana", "mssql", "postgres"],
)
def test_each_engine_owns_its_placeholder_clock_and_ping(name, placeholder, now_sql, ping_sql):
    strategy = d.get_dialect(name)
    assert strategy.placeholder == placeholder
    assert strategy.now_sql == now_sql and strategy.ping_sql == ping_sql
    assert strategy.render('SELECT ? FROM "T" WHERE "A" = ?') == f'SELECT {placeholder} FROM "T" WHERE "A" = {placeholder}'


def test_table_references_follow_each_engine_layout():
    assert d.get_dialect("hana").table_ref("SBODEMOMX", "OINV") == '"SBODEMOMX"."OINV"'
    assert d.get_dialect("mssql").table_ref("SBODemoMX", "OINV") == '"SBODemoMX"."dbo"."OINV"'
    assert d.get_dialect("postgres").table_ref("sbo_x", "OINV") == '"sbo_x"."OINV"'
    for strategy in map(d.get_dialect, d.DIALECTS):
        with pytest.raises(ValueError):
            strategy.table_ref("SBO_X", 'OINV"; DROP TABLE x; --')


def test_company_objects_are_validated_per_engine():
    mssql = d.get_dialect("mssql")
    for good in ("SBODemoUS", "SBO_MX-2", "1COMPANY"):
        assert mssql.validate_company_object(good) == good
    for bad in ("master", "TempDB", "msdb", "model", "a]b", "[x]", 'a"b', "a.b", "a b", "a;b", "SBO$X", "", "x" * 129):
        with pytest.raises(ValueError):
            mssql.validate_company_object(bad)
    assert d.get_dialect("hana").validate_company_object("SBO$X") == "SBO$X"
    assert [c.schema for c in parse_companies("mx_a=SBO_A,mx_b=SBO-B", "mssql")] == ["SBO_A", "SBO-B"]
    with pytest.raises(B1ConfigurationError):
        parse_companies("mx_a=master", "mssql")
    with pytest.raises(B1ConfigurationError):
        parse_companies("mx_a=SBO$A", "mssql")
    assert [c.schema for c in parse_companies("mx_a=SBO$A", "hana")] == ["SBO$A"]


def test_default_ports_and_required_settings_come_from_the_strategy():
    assert _config("hana").port == 30015 and _config("hana").missing == []
    mssql = _config("mssql")
    assert mssql.port == 1433 and mssql.missing == [], "SQL Server needs no SAP_B1_DATABASE: companies are databases"
    assert _config("mssql", port="11433").port == 11433
    postgres = _config("postgres")
    assert postgres.port == 5432 and postgres.missing == ["SAP_B1_DATABASE"]
    unknown = _config("oracle")
    assert unknown.missing == ["SAP_B1_DIALECT"] and unknown.port == 0
    assert _config("mssql", companies="mx_a=master").missing == ["SAP_B1_COMPANIES"]
    with pytest.raises(B1ConfigurationError):
        _config("mssql", companies="mx_a=master", strict_companies=True)


def test_the_sql_server_connection_uses_odbc_driver_18_and_escapes_every_value():
    mssql = d.get_dialect("mssql")
    text = mssql.connection_string(_config("mssql", ssl_validate_certificate=False))
    parts = text.rstrip(";").split(";")
    assert parts[0] == "DRIVER={ODBC Driver 18 for SQL Server}"
    assert "SERVER={tcp:sql-host,1433}" in parts and "UID={b1_reader}" in parts
    assert "PWD={not;a}}real}" in text, "a ; or } in the password must not end the value"
    assert "Encrypt=yes" in parts and "TrustServerCertificate=yes" in parts
    assert "ApplicationIntent=ReadOnly" in parts and not any(p.startswith("DATABASE=") for p in parts)
    strict = mssql.connection_string(_config("mssql", encrypt=False, database="SBO_A"))
    assert "Encrypt=no" in strict and "TrustServerCertificate=no" in strict and "DATABASE={SBO_A}" in strict
    injected = mssql.connection_string(_config("mssql", host="sql-host;Encrypt=no"))
    assert "SERVER={tcp:sql-host;Encrypt=no,1433}" in injected
    assert injected.replace("{tcp:sql-host;Encrypt=no,1433}", "{}").count("Encrypt=") == 1
    with pytest.raises(ValueError):
        mssql.connection_string(_config("mssql", database="master"))


def test_paging_is_limit_on_hana_and_offset_fetch_on_sql_server():
    plan = _plan("INV1")
    after = (10, 3)
    hana, _ = q.select_sql(plan, "SBO_X", dialect="hana", mode="full", after_key=after)
    mssql, _ = q.select_sql(plan, "SBO_X", dialect="mssql", mode="full", after_key=after)
    postgres, _ = q.select_sql(plan, "SBO_X", dialect="postgres", mode="full", after_key=after)
    assert hana.endswith(f'ORDER BY t."DocEntry", t."LineNum" LIMIT {plan.page_size}')
    assert postgres.endswith(f'ORDER BY t."DocEntry", t."LineNum" LIMIT {plan.page_size}')
    assert mssql.endswith(f'ORDER BY t."DocEntry", t."LineNum" OFFSET 0 ROWS FETCH NEXT {plan.page_size} ROWS ONLY')
    assert 'FROM "SBO_X"."dbo"."INV1" t JOIN "SBO_X"."dbo"."OINV" h' in mssql
    assert 'FROM "SBO_X"."INV1" t JOIN "SBO_X"."OINV" h' in hana
    assert hana.replace(f" LIMIT {plan.page_size}", "") == mssql.replace(
        f" OFFSET 0 ROWS FETCH NEXT {plan.page_size} ROWS ONLY", ""
    ).replace('"."dbo"."', '"."'), "the engines differ only in paging and the table reference"
    assert d.get_dialect("hana").limit_clause(5, 10) == " LIMIT 5 OFFSET 10"
    assert d.get_dialect("mssql").limit_clause(5, 10) == " OFFSET 10 ROWS FETCH NEXT 5 ROWS ONLY"
    for strategy in map(d.get_dialect, d.DIALECTS):
        with pytest.raises(ValueError):
            strategy.order_and_limit([], 5)
        with pytest.raises(ValueError):
            strategy.limit_clause(-1)


@pytest.mark.parametrize("name", d.DIALECTS)
def test_every_catalogue_entity_renders_for_every_engine(name):
    strategy = d.get_dialect(name)
    for entity in _catalogue():
        plan = q.plan_from_config(entity)
        modes = ["full"]
        if plan.incremental_capable:
            modes.append("incremental")
        if plan.date_field:
            modes.append("historical")
        for mode in modes:
            mark = None
            if mode == "incremental":
                mark = (
                    q.Watermark.parse(plan.watermark_kind, "2026-01-02T03:04:05")
                    if plan.watermark_kind == q.WATERMARK_UPDATE_TS
                    else q.Watermark.from_number(7)
                )
            after = tuple(range(len(plan.primary_key))) or None
            sql, params = q.select_sql(
                plan, "SBO_X", dialect=strategy, mode=mode, watermark=mark, after_key=after,
                from_date="2026-01-01" if mode == "historical" else None,
            )
            assert strategy.render(sql).count(strategy.placeholder) == len(params), (name, plan.entity, mode)
            assert strategy.table_ref("SBO_X", plan.table) in sql
            if plan.primary_key:
                assert sql.endswith(strategy.limit_clause(plan.page_size)), (name, plan.entity)
            else:
                assert "ORDER BY" not in sql
        count, _params = q.count_sql(plan, "SBO_X", dialect=strategy)
        assert count.startswith("SELECT COUNT(*) FROM (SELECT ") and count.endswith(") counted")
        assert "ORDER BY" not in count, "a derived table on SQL Server cannot carry ORDER BY"


def test_date_arithmetic_is_engine_specific():
    hana, mssql, postgres = (d.get_dialect(name) for name in d.DIALECTS)
    column = 't."DocDate"'
    assert hana.age_in_days(column) == 'DAYS_BETWEEN(t."DocDate", CURRENT_DATE)'
    assert mssql.age_in_days(column) == 'DATEDIFF(day, t."DocDate", CURRENT_TIMESTAMP)'
    assert postgres.age_in_days(column) == '(CAST(CURRENT_DATE AS DATE) - CAST(t."DocDate" AS DATE))'
    assert hana.add_days(column, 30) == 'ADD_DAYS(t."DocDate", 30)'
    assert mssql.add_days(column, -7) == 'DATEADD(day, -7, t."DocDate")'
    assert postgres.add_days("?", 2) == "(? + 2 * INTERVAL '1 day')"
    assert hana.days_between("?", column) == 'DAYS_BETWEEN(?, t."DocDate")'
    assert mssql.days_between("?", column) == 'DATEDIFF(day, ?, t."DocDate")'
    for strategy in (hana, mssql, postgres):
        for bad in ("1; DROP TABLE x", 1.5, True, None):
            with pytest.raises(ValueError):
                strategy.add_days(column, bad)


def test_authentication_failures_are_recognised_per_engine():
    hana, mssql, postgres = (d.get_dialect(name) for name in d.DIALECTS)
    assert hana.is_auth_error("(10, 'authentication failed')")
    assert mssql.is_auth_error("('28000', \"[28000] [Microsoft][ODBC Driver 18 for SQL Server][SQL Server]Login failed for user '***'. (18456)\")")
    assert postgres.is_auth_error('FATAL:  password authentication failed for user "***"')
    for strategy in (hana, mssql, postgres):
        assert not strategy.is_auth_error("could not connect to server: Connection refused")
        assert not strategy.is_auth_error("[08001] TCP Provider: Error code 0x2749 (10057)")


def test_bronze_names_come_from_the_catalogue_whatever_case_the_driver_reports():
    plan = _plan("INV1")
    row = tuple(range(len(plan.columns))) + (None, None)
    expected = list(plan.output_columns)
    for reported in (
        [*plan.columns, "_wm_date", "_wm_ts"],
        [c.upper() for c in plan.columns] + ["_WM_DATE", "_WM_TS"],
        [c.lower() for c in plan.columns] + ["_wm_date", "_wm_ts"],
    ):
        records = q.rows_to_records(plan, "mx_a", reported, [row])
        assert list(records[0]) == expected
    with pytest.raises(ValueError, match="do not match"):
        q.rows_to_records(plan, "mx_a", list(reversed(plan.columns)), [row])
    with pytest.raises(ValueError, match="do not match"):
        q.rows_to_records(plan, "mx_a", list(plan.columns)[:-1], [row])


def test_driver_imports_stay_lazy():
    probe = (
        "import sys\n"
        f"sys.path.insert(0, {str(CARTRIDGE)!r})\n"
        "import app.core.b1_dialects, app.core.b1_source, app.services.b1_queries, app.services.b1_reader\n"
        "import app.services.source_counts_mapping, app.services.bronze_parquet\n"
        "print(sorted(n for n in sys.modules if n.split('.')[0] in {'hdbcli', 'pyodbc', 'psycopg2'}))\n"
    )
    proc = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "[]"


def test_a_missing_driver_is_a_clear_source_error_that_does_not_trip_the_breaker(monkeypatch):
    b1_source.CartridgeCircuitBreaker.reset()
    monkeypatch.setitem(sys.modules, "pyodbc", None)
    with pytest.raises(b1_source.B1SourceError, match="pyodbc is not installed"):
        b1_source.open_connection(_config("mssql"))
    probe = b1_source.probe_source(_config("mssql"))
    assert probe["ok"] is False and "pyodbc is not installed" in probe["error"]
    assert b1_source.CartridgeCircuitBreaker.snapshot()["failures"] == 0
    b1_source.CartridgeCircuitBreaker.reset()


def _engine_branches(tree: ast.AST) -> list[int]:
    lines = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        for operand in (node.left, *node.comparators):
            values = operand.elts if isinstance(operand, (ast.Tuple, ast.List, ast.Set)) else [operand]
            if any(isinstance(v, ast.Constant) and v.value in d.DIALECTS for v in values):
                lines.append(node.lineno)
    return lines


@pytest.mark.parametrize("path", STRATEGY_FREE_MODULES, ids=lambda p: p.name)
def test_engine_differences_live_only_in_the_strategies(path):
    text = path.read_text(encoding="utf-8")
    assert _engine_branches(ast.parse(text)) == [], f"{path.name} branches on a dialect name"
    code = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )
    assert not ENGINE_SQL.search(code.split('"""', 2)[-1] if path.name == "agent.py" else code), (
        f"{path.name} writes engine-specific SQL: {ENGINE_SQL.search(code).group(0)!r}"
    )
