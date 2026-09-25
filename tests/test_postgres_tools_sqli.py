from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
MCP_INFRA_DIR = REPO_ROOT / "mcp-infra"
CARTRIDGES_ROOT = REPO_ROOT / "cartridges"


_MCP_INFRA_TEST_ENV = {
    "AIRFLOW_USER": "ci",
    "AIRFLOW_PASSWORD": "ci",
    "PG_PASSWORD": "ci",
    "SUPERSET_USER": "ci",
    "SUPERSET_PASSWORD": "ci",
    "INTERNAL_API_KEY": "test-internal-api-key-not-default-aaaaaaaaaaaaaaaaa",
    "DATABASE_URL": "postgresql+psycopg2://test:test@postgres:5432/modecissions",
    "GOLD_DATABASE_URL": "postgresql+psycopg2://test:test@postgres_gold:5433/modecissions_gold",
    "MINIO_ACCESS_KEY": "test-minio-access",
    "MINIO_SECRET_KEY": "test-minio-secret",
}
for _k, _v in _MCP_INFRA_TEST_ENV.items():
    os.environ.setdefault(_k, _v)

SERVICE_PATH_MARKERS = (
    "/cartridges/",
    "/console",
    "/refinement",
    "/vault",
    "/workspace",
)


def _purge_app_modules() -> None:
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]


@pytest.fixture(autouse=True)
def _clean_app_modules():
    _purge_app_modules()
    saved_path = list(sys.path)
    yield
    sys.path[:] = saved_path
    _purge_app_modules()


@pytest.fixture
def validators():
    sys.path[:] = [
        p for p in sys.path if not any(marker in p for marker in SERVICE_PATH_MARKERS)
    ]
    sys.path.insert(0, str(MCP_INFRA_DIR))
    _purge_app_modules()
    return importlib.import_module("app.tools._validators")


def test_validator_accepts_simple_identifier(validators):
    assert validators.validate_identifier("users", "table") == "users"
    assert validators.validate_identifier("public", "schema") == "public"
    assert validators.validate_identifier("_x9", "any") == "_x9"


@pytest.mark.parametrize(
    "bad",
    [
        'users"; DROP TABLE x; --',
        "users; DROP TABLE x;",
        "users UNION SELECT password FROM x",
        "1users",
        "users.passwords",
        "users\x00",
        "users--",
        "p‮ublic",
        "пользователи",
        "",
        " users",
        "users ",
    ],
)
def test_validator_rejects_sql_injection(validators, bad):
    with pytest.raises(ValueError, match="Invalid"):
        validators.validate_identifier(bad, "table")


def test_validator_rejects_non_string(validators):
    with pytest.raises(ValueError, match="Invalid"):
        validators.validate_identifier(None, "table")
    with pytest.raises(ValueError, match="Invalid"):
        validators.validate_identifier(123, "table")


@pytest.mark.parametrize("good", [1, 10, 100, "50"])
def test_validate_bounded_int_accepts_in_range(validators, good):
    assert validators.validate_bounded_int(good, "n", lo=1, hi=100) == int(good)


@pytest.mark.parametrize(
    "bad",
    [
        "10; DROP TABLE x; --",
        "abc",
        "",
        None,
        1.5,
        True,
        False,
    ],
)
def test_validate_bounded_int_rejects_invalid(validators, bad):
    with pytest.raises(ValueError):
        validators.validate_bounded_int(bad, "n", lo=1, hi=100)


@pytest.mark.parametrize("oob", [0, -1, 101, 1000])
def test_validate_bounded_int_rejects_out_of_range(validators, oob):
    with pytest.raises(ValueError, match="must be 1..100"):
        validators.validate_bounded_int(oob, "n", lo=1, hi=100)


@pytest.fixture
def postgres_tool():
    sys.path[:] = [
        p for p in sys.path if not any(marker in p for marker in SERVICE_PATH_MARKERS)
    ]
    sys.path.insert(0, str(MCP_INFRA_DIR))
    _purge_app_modules()
    return importlib.import_module("app.tools.postgres")


def test_postgres_get_sample_rejects_sqli_in_table(postgres_tool):
    with patch.object(postgres_tool, "_conn") as mock_conn:
        with pytest.raises(ValueError, match="Invalid table"):
            postgres_tool.postgres_get_sample(
                table='users"; DROP TABLE x; --', schema="public", n=10
            )
        mock_conn.assert_not_called()


def test_postgres_get_sample_rejects_sqli_in_schema(postgres_tool):
    with patch.object(postgres_tool, "_conn") as mock_conn:
        with pytest.raises(ValueError, match="Invalid schema"):
            postgres_tool.postgres_get_sample(
                table="users", schema="public; DROP TABLE x; --", n=10
            )
        mock_conn.assert_not_called()


def test_postgres_get_sample_rejects_unicode_injection(postgres_tool):
    with patch.object(postgres_tool, "_conn") as mock_conn:
        with pytest.raises(ValueError, match="Invalid schema"):
            postgres_tool.postgres_get_sample(table="users", schema="p‮ublic", n=10)
        mock_conn.assert_not_called()


def test_postgres_get_sample_rejects_invalid_n(postgres_tool):
    with patch.object(postgres_tool, "_conn") as mock_conn:
        with pytest.raises(ValueError):
            postgres_tool.postgres_get_sample(
                table="users", schema="public", n="10; DROP TABLE x; --"
            )
        mock_conn.assert_not_called()


def test_postgres_get_sample_rejects_n_out_of_range(postgres_tool):
    with patch.object(postgres_tool, "_conn") as mock_conn:
        with pytest.raises(ValueError, match="must be 1..100"):
            postgres_tool.postgres_get_sample(table="users", schema="public", n=0)
        with pytest.raises(ValueError, match="must be 1..100"):
            postgres_tool.postgres_get_sample(table="users", schema="public", n=9999)
        mock_conn.assert_not_called()


def test_postgres_get_sample_accepts_valid_inputs(postgres_tool):
    mock_cur = MagicMock()
    mock_cur.fetchall.return_value = []
    mock_cur.description = []
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur
    with patch.object(postgres_tool, "_conn", return_value=mock_conn):
        result = postgres_tool.postgres_get_sample(table="users", schema="public", n=10)
    assert result == {"rows": [], "columns": [], "count": 0}
    executed = mock_cur.execute.call_args[0][0]
    assert executed == 'SELECT * FROM "public"."users" LIMIT 10'


def test_postgres_execute_query_rejects_sqli_in_limit(postgres_tool):
    with patch.object(postgres_tool, "_conn") as mock_conn:
        with pytest.raises(ValueError):
            postgres_tool.postgres_execute_query(
                sql="SELECT 1", limit="10; DROP TABLE x; --"
            )
        mock_conn.assert_not_called()


def test_postgres_execute_query_rejects_limit_out_of_range(postgres_tool):
    with patch.object(postgres_tool, "_conn") as mock_conn:
        with pytest.raises(ValueError, match="must be 1..200"):
            postgres_tool.postgres_execute_query(sql="SELECT 1", limit=10_000)
        mock_conn.assert_not_called()


def test_postgres_execute_query_short_circuits_non_select(postgres_tool):
    result = postgres_tool.postgres_execute_query(sql="DROP TABLE x", limit=10)
    assert "error" in result


@pytest.mark.parametrize(
    "payload",
    [
        "SELECT 1; DROP TABLE users",
        "SELECT 1 ; UPDATE users SET role='admin' WHERE id=1",
        "WITH x AS (SELECT 1) SELECT * FROM x; DROP TABLE users",
        "SELECT 1) UNION SELECT password FROM users; --",
        "SELECT 1 -- ; DROP TABLE x\nUNION SELECT 1",
        "SELECT 1 /* ; */ UNION SELECT password FROM users",
    ],
)
def test_postgres_execute_query_rejects_multi_statement_and_comments(
    postgres_tool, payload
):
    with patch.object(postgres_tool, "_conn") as mock_conn:
        result = postgres_tool.postgres_execute_query(sql=payload, limit=10)
        assert (
            "error" in result
        ), f"payload {payload!r} should be rejected, got result {result!r}"
        mock_conn.assert_not_called()


@pytest.mark.parametrize(
    "bad",
    [
        "SELECT 1\x00; DROP TABLE users",
        "SELECT 1\x01 UNION SELECT password FROM users",
        "SELECT 1\x07",
        "SELECT 1\x1f",
    ],
)
def test_postgres_execute_query_rejects_control_chars(postgres_tool, bad):
    with patch.object(postgres_tool, "_conn") as mock_conn:
        result = postgres_tool.postgres_execute_query(sql=bad, limit=10)
        assert "error" in result, f"payload {bad!r} should be rejected"
        mock_conn.assert_not_called()


def test_postgres_execute_query_allows_whitespace_control_chars(postgres_tool):
    mock_cur = MagicMock()
    mock_cur.fetchall.return_value = []
    mock_cur.description = []
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur
    with patch.object(postgres_tool, "_conn", return_value=mock_conn):
        result = postgres_tool.postgres_execute_query(
            sql="SELECT 1\n\tFROM dual", limit=10
        )
    assert "error" not in result


def test_postgres_execute_query_accepts_clean_select(postgres_tool):
    mock_cur = MagicMock()
    mock_cur.fetchall.return_value = []
    mock_cur.description = []
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur
    with patch.object(postgres_tool, "_conn", return_value=mock_conn):
        result = postgres_tool.postgres_execute_query(sql="SELECT 1", limit=10)
    assert "error" not in result
    executed = mock_cur.execute.call_args[0][0]
    assert executed == "SELECT * FROM (SELECT 1) _capped LIMIT 10"


def test_postgres_execute_query_strips_single_trailing_semicolon(postgres_tool):
    mock_cur = MagicMock()
    mock_cur.fetchall.return_value = []
    mock_cur.description = []
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur
    with patch.object(postgres_tool, "_conn", return_value=mock_conn):
        result = postgres_tool.postgres_execute_query(sql="SELECT 1;", limit=10)
    assert "error" not in result
    executed = mock_cur.execute.call_args[0][0]
    assert executed == "SELECT * FROM (SELECT 1) _capped LIMIT 10"


def test_postgres_list_tables_uses_parameterized_query(postgres_tool):
    mock_cur = MagicMock()
    mock_cur.fetchall.return_value = []
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur
    dangerous = 'x"; DROP TABLE users; --'
    with patch.object(postgres_tool, "_conn", return_value=mock_conn):
        postgres_tool.postgres_list_tables(schema=dangerous)
    sql, params = mock_cur.execute.call_args[0]
    assert "%s" in sql, "schema must be a bound parameter, not interpolated"
    assert params == (dangerous,)
    assert dangerous not in sql


def test_postgres_get_table_schema_uses_parameterized_query(postgres_tool):
    mock_cur = MagicMock()
    mock_cur.fetchall.return_value = []
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur
    dangerous_table = "x'; DROP TABLE users; --"
    dangerous_schema = "y'; DROP TABLE roles; --"
    with patch.object(postgres_tool, "_conn", return_value=mock_conn):
        postgres_tool.postgres_get_table_schema(
            table=dangerous_table, schema=dangerous_schema
        )
    sql, params = mock_cur.execute.call_args[0]
    assert "%s" in sql
    assert params == (dangerous_schema, dangerous_table)
    assert dangerous_table not in sql
    assert dangerous_schema not in sql


def test_cartridge_preview_rejects_limit_sqli_specifically(cartridges_tool):
    with patch.object(cartridges_tool, "_duckdb") as mock_duck:
        with pytest.raises(ValueError, match="limit"):
            cartridges_tool.cartridge_preview(
                cartridge_id="replicon",
                entity="TimeEntry",
                limit="10; DROP TABLE x; --",
            )
        mock_duck.assert_not_called()


def test_cartridge_query_kb_rejects_limit_sqli(cartridges_tool):
    with patch.object(cartridges_tool, "_duckdb") as mock_duck:
        with pytest.raises(ValueError, match="limit"):
            cartridges_tool.cartridge_query_kb(
                cartridge_id="replicon",
                sql="SELECT 1",
                limit="100; DROP TABLE x; --",
            )
        mock_duck.assert_not_called()


@pytest.fixture
def cartridges_tool(postgres_tool):
    return importlib.import_module("app.tools.cartridges")


def test_bronze_path_rejects_sqli_in_cartridge_id(cartridges_tool):
    with pytest.raises(ValueError, match="Invalid cartridge_id"):
        cartridges_tool._bronze_path(
            cartridge_id="x') UNION SELECT * FROM 's3://other/p.parquet'; --",
            entity="users",
        )


def test_bronze_path_rejects_sqli_in_entity(cartridges_tool):
    with pytest.raises(ValueError, match="Invalid entity"):
        cartridges_tool._bronze_path(
            cartridge_id="replicon",
            entity="x/load_date=*/*.parquet') UNION SELECT 1; --",
        )


def test_bronze_path_accepts_valid_identifiers(cartridges_tool):
    out = cartridges_tool._bronze_path(cartridge_id="replicon", entity="TimeEntry")
    assert "/raw/replicon/TimeEntry/" in out
    assert out.startswith("s3://")


def test_bronze_path_uses_tenant_workspace_scope_when_available(cartridges_tool):
    out = cartridges_tool._bronze_path(
        cartridge_id="replicon",
        entity="TimeEntry",
        security_context={
            "trusted": True,
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
        },
    )

    assert "/raw/replicon/TimeEntry/tenant_id=tenant-1/workspace_id=workspace-1/" in out
    assert "/load_date=*/batch_id=*/*.parquet" in out


def test_scope_cartridge_sql_rewrites_kb_read_paths_when_context_is_scoped(
    cartridges_tool,
):
    out = cartridges_tool._scope_cartridge_sql(
        "SELECT * FROM read_parquet('s3://lakehouse/raw/replicon/TimeEntry/**/*.parquet')",
        "replicon",
        security_context={
            "trusted": True,
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
        },
    )

    assert "raw/replicon/TimeEntry/tenant_id=tenant-1/workspace_id=workspace-1/" in out


def test_scope_cartridge_sql_rewrites_replicon_shared_kb_inputs_when_context_is_scoped(
    cartridges_tool,
):
    out = cartridges_tool._scope_cartridge_sql(
        """
        SELECT *
        FROM read_parquet('s3://lakehouse/raw/fx_rates/mxn_usd/fx_rates.parquet') fx
        JOIN read_parquet('s3://lakehouse/raw/excel_billing/invoices/load_date=*/*.parquet') inv
          ON true
        """,
        "replicon",
        security_context={
            "trusted": True,
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
        },
    )

    assert (
        "raw/fx_rates/mxn_usd/tenant_id=tenant-1/workspace_id=workspace-1/fx_rates.parquet"
        in out
    )
    assert (
        "raw/excel_billing/invoices/tenant_id=tenant-1/workspace_id=workspace-1/load_date=*/*.parquet"
        in out
    )
    assert "raw/fx_rates/mxn_usd/fx_rates.parquet" not in out
    assert "raw/excel_billing/invoices/load_date=*/*.parquet" not in out


def test_cartridge_preview_rejects_limit_sqli(cartridges_tool):
    with patch.object(cartridges_tool, "_duckdb") as mock_duck:
        with pytest.raises(ValueError):
            cartridges_tool.cartridge_preview(
                cartridge_id="replicon",
                entity="TimeEntry",
                limit="10; DROP TABLE x; --",
            )
        mock_duck.assert_not_called()


def test_cartridge_preview_error_does_not_leak_sql_or_path(cartridges_tool):
    conn = MagicMock()
    conn.execute.side_effect = RuntimeError(
        "read_parquet('s3://lakehouse/raw/replicon/TimeEntry/secret.parquet') failed"
    )
    with patch.object(cartridges_tool, "_duckdb", return_value=conn):
        result = cartridges_tool.cartridge_preview(
            cartridge_id="replicon",
            entity="TimeEntry",
            security_context={
                "trusted": True,
                "tenant_id": "tenant-1",
                "workspace_id": "workspace-1",
            },
        )

    assert result["error"] == "preview_failed"
    assert result["reason"] == "DuckDB preview failed"
    serialized = str(result)
    assert "s3://" not in serialized
    assert "read_parquet" not in serialized
    assert "secret.parquet" not in serialized
    conn.close.assert_called_once()


SAP_CARTRIDGES = ("sap_hcm", "sap_s4hana", "sap_successfactors", "sap_b1")


def _load_sap_mcp_module(cartridge_id: str):
    cart_dir = CARTRIDGES_ROOT / cartridge_id
    sys.path[:] = [
        p for p in sys.path if "/cartridges/" not in p and "/mcp-infra" not in p
    ]
    sys.path.insert(0, str(cart_dir))
    _purge_app_modules()
    return importlib.import_module("app.mcp_server")


@pytest.mark.parametrize("cartridge_id", SAP_CARTRIDGES)
def test_sap_local_validator_rejects_sqli(cartridge_id, monkeypatch):
    monkeypatch.setenv(
        "FIELD_ENCRYPTION_KEY", "_lAbgL_v0c1jp9R_jHkR1lHl4o4-XfcExlIRY60n_5o="
    )
    mod = _load_sap_mcp_module(cartridge_id)
    with pytest.raises(ValueError, match="Invalid entity"):
        mod._validate_identifier("X') UNION SELECT 1; --", "entity")


@pytest.mark.parametrize("cartridge_id", SAP_CARTRIDGES)
def test_sap_local_validator_accepts_valid(cartridge_id, monkeypatch):
    monkeypatch.setenv(
        "FIELD_ENCRYPTION_KEY", "_lAbgL_v0c1jp9R_jHkR1lHl4o4-XfcExlIRY60n_5o="
    )
    mod = _load_sap_mcp_module(cartridge_id)
    assert mod._validate_identifier("TimeEntry", "entity") == "TimeEntry"
    assert mod._validate_bounded_int(50, "limit", lo=1, hi=200) == 50


def test_no_unvalidated_fstring_sql_in_postgres_tool():
    src = (MCP_INFRA_DIR / "app" / "tools" / "postgres.py").read_text(encoding="utf-8")
    assert "from app.tools._validators import" in src
    assert "validate_identifier(schema" in src
    assert "validate_identifier(table" in src
    assert "validate_bounded_int" in src


def test_no_unvalidated_fstring_sql_in_cartridges_tool():
    src = (MCP_INFRA_DIR / "app" / "tools" / "cartridges.py").read_text(
        encoding="utf-8"
    )
    assert "from app.tools._validators import" in src
    assert "validate_identifier(cartridge_id" in src
    assert "validate_identifier(entity" in src


@pytest.mark.parametrize("cartridge_id", SAP_CARTRIDGES)
def test_no_unvalidated_fstring_sql_in_sap_mcp_server(cartridge_id):
    src = (CARTRIDGES_ROOT / cartridge_id / "app" / "mcp_server.py").read_text(
        encoding="utf-8"
    )
    assert "_validate_identifier" in src
    assert "_validate_bounded_int" in src
    assert "_validate_identifier(entity" in src
