from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest


TENANT = "11111111-1111-1111-1111-111111111111"
WORKSPACE = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
OTHER_TENANT = "22222222-2222-2222-2222-222222222222"
OTHER_WORKSPACE = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
SCOPED = (
    f"s3://lakehouse/raw/sap_successfactors/x/tenant_id={TENANT}"
    f"/workspace_id={WORKSPACE}/x.csv"
)

EXFIL_SLICE_CONCAT = f"""SELECT count(*) AS n
FROM read_csv(
  '{SCOPED}'[1:0]
  || '/etc/passwd',
  header=false,
  all_varchar=true
)"""

EXFIL_ENVIRON = f"""SELECT *
FROM read_csv_auto(
  '{SCOPED}'[1:0] || '/pr' || 'oc/self/environ',
  header=false,
  all_varchar=true
)"""

EXFIL_CROSS_WORKSPACE = (
    "SELECT * FROM read_csv("
    f"'s3://lakehouse/raw/sap_successfactors/x/tenant_id={OTHER_TENANT}"
    f"/workspace_id={OTHER_WORKSPACE}/x.csv', header=false)"
)

EXFIL_CONCAT_FIRST = "SELECT * FROM read_csv('/etc' || '/passwd', header=false)"

EXFIL_SLICE_AFTER_CONCAT = (
    f"SELECT * FROM read_csv(('{SCOPED}' || '/etc/passwd')[1:200], header=false)"
)

EXFIL_CTE_LAUNDERING = f"""WITH laundered AS (
  SELECT '{SCOPED}'[1:0] || '/etc/passwd' AS p
)
SELECT * FROM read_csv((SELECT p FROM laundered), header=false)"""

EXFIL_ALIAS = (
    f"SELECT * FROM read_csv('{SCOPED}'[1:0] || '/etc/passwd', header=false) AS t(a)"
)

EXFIL_NESTED_READER = (
    "SELECT * FROM read_csv(read_csv('/etc/passwd', header=false), header=false)"
)

EXFIL_CAST = (
    f"SELECT * FROM read_csv(CAST('{SCOPED}'[1:0] || '/etc/passwd' AS VARCHAR),"
    " header=false)"
)

EXFIL_PARAMETER = "SELECT * FROM read_csv(?, header=false)"

EXFIL_UNICODE = "SELECT * FROM read․csv('/etc/passwd', header=false)"

EXFIL_LIST = f"SELECT * FROM read_csv(['{SCOPED}', '/etc/passwd'], header=false)"

EXFIL_ARITHMETIC = (
    "SELECT * FROM read_csv('/etc/passwd' || repeat('a', 1 + 1), header=false)"
)

EXFIL_PIVOT_SCOPE_LAUNDERING = f"""WITH decoy AS (
  SELECT * FROM read_parquet(
    's3://lakehouse/raw/sap_successfactors/x/tenant_id={TENANT}/workspace_id={WORKSPACE}/x.parquet'
  )
),
evil AS (
  PIVOT '/usr/local/lib/python3.12/site-packages/pyarrow/tests/data/parquet/v0.7.1.parquet'
  ON cut USING count(*)
)
SELECT count(*) AS n
FROM evil
WHERE EXISTS (
  WITH "/usr/local/lib/python3.12/site-packages/pyarrow/tests/data/parquet/v0.7.1.parquet"
       AS (SELECT 1)
  SELECT 1
)"""

BLOCKED = [
    pytest.param(EXFIL_SLICE_CONCAT, id="slice-then-concat"),
    pytest.param(EXFIL_ENVIRON, id="proc-self-environ"),
    pytest.param(EXFIL_CONCAT_FIRST, id="concat-only"),
    pytest.param(EXFIL_SLICE_AFTER_CONCAT, id="slice-after-concat"),
    pytest.param(EXFIL_CTE_LAUNDERING, id="cte-laundering"),
    pytest.param(EXFIL_ALIAS, id="alias"),
    pytest.param(EXFIL_NESTED_READER, id="nested-reader"),
    pytest.param(EXFIL_CAST, id="cast"),
    pytest.param(EXFIL_PARAMETER, id="parameter"),
    pytest.param(EXFIL_UNICODE, id="unicode-nfkc"),
    pytest.param(EXFIL_LIST, id="dynamic-list"),
    pytest.param(EXFIL_ARITHMETIC, id="arithmetic"),
    pytest.param(EXFIL_CROSS_WORKSPACE, id="cross-workspace-uri"),
    pytest.param(EXFIL_PIVOT_SCOPE_LAUNDERING, id="pivot-cte-scope-laundering"),
]

VALID = [
    pytest.param(f"SELECT * FROM read_csv('{SCOPED}', header=false)", id="scoped-csv"),
    pytest.param(
        "SELECT * FROM read_parquet("
        f"'s3://lakehouse/silver/sap_successfactors/user/tenant_id={TENANT}"
        f"/workspace_id={WORKSPACE}/part.parquet')",
        id="scoped-parquet",
    ),
    pytest.param("SELECT 1 AS n", id="no-reader"),
    pytest.param(
        "SELECT * FROM read_parquet("
        "'s3://{bucket}/raw/sap_successfactors/x/**/*.parquet')",
        id="server-resolved-parquet",
    ),
]


def _load_cartridge_tools(monkeypatch):
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "mcp-infra"))
    for key, value in {
        "AIRFLOW_URL": "http://airflow:8080/airflow",
        "AIRFLOW_USER": "admin",
        "AIRFLOW_PASSWORD": "admin",
        "PG_PASSWORD": "postgres",
        "SUPERSET_USER": "admin",
        "SUPERSET_PASSWORD": "admin",
    }.items():
        monkeypatch.setenv(key, value)
    try:
        return importlib.import_module("app.tools.cartridges")
    finally:
        try:
            sys.path.remove(str(root / "mcp-infra"))
        except ValueError:
            pass


class _SpyConnection:
    def __init__(self) -> None:
        self.executed: list[str] = []

    def execute(self, sql, *args, **kwargs):
        self.executed.append(str(sql))
        raise AssertionError("a rejected statement reached conn.execute")

    def close(self) -> None:
        return None


def _context() -> dict:
    return {"tenant_id": TENANT, "workspace_id": WORKSPACE, "trusted": True}


def _call(tools, monkeypatch, sql: str) -> tuple[dict, _SpyConnection]:
    spy = _SpyConnection()
    monkeypatch.setattr(tools, "_duckdb", lambda: spy)
    monkeypatch.setattr(
        tools, "replicon_generic_query_block_reason", lambda *a, **k: None
    )
    result = tools.cartridge_query_kb(
        cartridge_id="sap_successfactors",
        sql=sql,
        limit=10,
        security_context=_context(),
    )
    return result, spy


@pytest.mark.parametrize("sql", BLOCKED)
def test_reader_arguments_outside_the_scoped_sandbox_are_blocked(monkeypatch, sql):
    tools = _load_cartridge_tools(monkeypatch)
    result, spy = _call(tools, monkeypatch, sql)

    assert spy.executed == []
    assert result.get("error") or result.get("data_status") == "unavailable"


@pytest.mark.parametrize("sql", BLOCKED)
def test_rejections_never_leak_sql_paths_or_secrets(monkeypatch, sql):
    tools = _load_cartridge_tools(monkeypatch)
    result, _ = _call(tools, monkeypatch, sql)

    blob = repr(result)
    for needle in ("/etc/passwd", "/proc", "environ", "s3://", "read_csv", "SELECT"):
        assert needle not in blob


@pytest.mark.parametrize("sql", VALID)
def test_scoped_reads_are_still_accepted(monkeypatch, sql):
    tools = _load_cartridge_tools(monkeypatch)
    result, spy = _call(tools, monkeypatch, sql)

    assert len(spy.executed) == 1
    assert result.get("error") == "query_failed"


def test_second_validation_runs_immediately_before_execute(monkeypatch):
    tools = _load_cartridge_tools(monkeypatch)
    spy = _SpyConnection()
    monkeypatch.setattr(tools, "_duckdb", lambda: spy)
    monkeypatch.setattr(
        tools, "replicon_generic_query_block_reason", lambda *a, **k: None
    )
    monkeypatch.setattr(
        tools,
        "_scope_cartridge_sql",
        lambda *a, **k: "SELECT * FROM read_csv('/etc/passwd', header=false)",
    )
    result = tools.cartridge_query_kb(
        cartridge_id="sap_successfactors",
        sql=f"SELECT * FROM read_csv('{SCOPED}', header=false)",
        limit=10,
        security_context=_context(),
    )

    assert spy.executed == []
    assert result.get("error") or result.get("data_status") == "unavailable"


def test_public_mcp_guard_uses_the_same_closed_ast_policy(monkeypatch, caplog):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("INTERNAL_API_KEY", "m" * 32)
    monkeypatch.setenv("SECURITY_CONTEXT_SIGNING_KEY", "s" * 32)
    tools = _load_cartridge_tools(monkeypatch)
    main = importlib.import_module("app.main")

    with pytest.raises(Exception) as excinfo:
        main._validate_cartridge_query_sql(
            _context(), "sap_successfactors", EXFIL_PIVOT_SCOPE_LAUNDERING
        )

    assert getattr(excinfo.value, "status_code", None) == 403
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__context__ is None
    assert "/usr/local" not in str(excinfo.value)
    assert "/usr/local" not in caplog.text
    assert "PIVOT" not in caplog.text
    assert tools is not None
