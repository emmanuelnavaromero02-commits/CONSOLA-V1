from __future__ import annotations

import threading
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi import HTTPException

from refinement.app import main as refinement_main
from refinement.app.duckdb_engine import DuckDBEngine


TENANT = "tenant-a"
WORKSPACE = "workspace-a"
SCOPED_URI = (
    "s3://lakehouse/raw/sap_successfactors/EmpEmployment/"
    f"tenant_id={TENANT}/workspace_id={WORKSPACE}/data.parquet"
)

TABLE_FUNCTION_CANARIES = [
    (
        "SELECT * FROM read_csv_auto('/pr' || 'oc/self/environ', "
        "header=false, all_varchar=true)"
    ),
    "SELECT * FROM read_csv_auto('/etc/passwd')",
    "SELECT * FROM read_json_auto('file:///etc/passwd')",
    "SELECT * FROM read_text('/etc/passwd')",
    "SELECT * FROM read_blob('/proc/self/environ')",
    "SELECT * FROM read_parquet('file:///etc/passwd')",
    "SELECT * FROM read_parquet('/proc/self/environ')",
    "SELECT * FROM read_parquet('/etc/passwd')",
    (
        "SELECT * FROM read_parquet("
        f"'{SCOPED_URI.rsplit('/', 1)[0]}/%2e%2e/%2e%2e/etc/passwd')"
    ),
    "SELECT * FROM read＿csv＿auto('/etc/passwd')",
    f"SELECT * FROM unknown_table_reader('{SCOPED_URI}')",
    f"SELECT * FROM read_parquet('{SCOPED_URI}' || '.backup')",
    "SELECT * FROM read_parquet(?)",
]


def _security_context() -> dict:
    return {
        "trusted": True,
        "source": "console",
        "role": "user",
        "workspace_role": "workspace_admin",
        "user_id": 41,
        "permissions": ["datasets.read", "datasets.write"],
        "tenant_id": TENANT,
        "workspace_id": WORKSPACE,
        "allowed_buckets": ["lakehouse"],
        "allowed_cartridges": ["sap_successfactors"],
        "allowed_prefixes": [
            "raw/sap_successfactors/EmpEmployment/"
            f"tenant_id={TENANT}/workspace_id={WORKSPACE}/"
        ],
    }


def _signed_body(sql: str) -> dict:
    return {
        "tool": "preview_transform",
        "args": {"sql": sql, "sources": [], "limit": 20},
        "security_context": refinement_main._sign_security_context(
            _security_context()
        ),
        "_verified_internal_service": "console",
    }


@pytest.mark.parametrize("sql", TABLE_FUNCTION_CANARIES)
def test_engine_blocks_table_function_and_path_canaries(sql: str) -> None:
    engine = object.__new__(DuckDBEngine)

    with pytest.raises(ValueError, match="safety policy") as exc:
        engine._validate_safe_sql(sql)

    assert "/proc" not in str(exc.value)
    assert "/etc" not in str(exc.value)


@pytest.mark.parametrize("sql", TABLE_FUNCTION_CANARIES)
def test_refinement_boundary_blocks_table_function_and_path_canaries(
    sql: str,
) -> None:
    with pytest.raises(HTTPException) as exc:
        refinement_main._require_sql_storage_scope(_signed_body(sql), sql, [])

    assert exc.value.status_code == 403
    assert "/proc" not in str(exc.value.detail)
    assert "/etc" not in str(exc.value.detail)


@pytest.mark.parametrize(
    "sql",
    [
        f"SELECT * FROM read_parquet('{SCOPED_URI}')",
        (
            "WITH scoped AS ("
            f"SELECT * FROM read_parquet('{SCOPED_URI}')"
            ") SELECT * FROM scoped"
        ),
        "WITH constants AS (SELECT 1 AS value) SELECT * FROM constants",
        "SELECT 1 AS value",
        "SELECT * FROM generate_series(1, 3)",
        "SELECT * FROM UNNEST([1, 2, 3])",
    ],
)
def test_safe_scoped_and_storage_free_queries_remain_allowed(sql: str) -> None:
    engine = object.__new__(DuckDBEngine)
    engine._validate_safe_sql(sql)
    refinement_main._require_sql_storage_scope(_signed_body(sql), sql, [])


@pytest.mark.asyncio
async def test_mcp_http_blocks_original_exploit_before_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sql = TABLE_FUNCTION_CANARIES[0]
    preview = MagicMock(return_value={"schema": [], "data": []})
    monkeypatch.setattr(refinement_main.engine, "preview_sql", preview)
    pair_key = "p0-console-refinement-pair-key-more-than-32-characters"
    monkeypatch.setenv("INTERNAL_API_KEY_CONSOLE_TO_REFINEMENT", pair_key)
    body = _signed_body(sql)
    body.pop("_verified_internal_service")

    transport = httpx.ASGITransport(app=refinement_main.app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://refinement.test",
    ) as client:
        response = await client.post(
            "/mcp/invoke",
            headers={
                "x-api-key": pair_key,
                "x-internal-service": "console",
            },
            json=body,
        )

    assert response.status_code == 403
    assert "/proc" not in response.text
    assert "environ" not in response.text
    preview.assert_not_called()


def test_effective_sql_is_revalidated_immediately_before_execute() -> None:
    engine = object.__new__(DuckDBEngine)
    engine._duckdb_lock = threading.RLock()
    connection = MagicMock()
    cursor = connection.execute.return_value
    cursor.description = [("value", "INTEGER")]
    cursor.fetchall.return_value = [(1,)]
    engine.get_rls_filters = lambda sql, _ctx: (sql, [])
    engine._conn = lambda: connection
    engine._inject_bucket = lambda sql: sql
    engine._scope_storage_sql = lambda *_args: TABLE_FUNCTION_CANARIES[0]
    engine._inject_latest_date = lambda sql, *_args: sql
    engine._validate_scoped_storage_sql = lambda *_args: None

    result = engine.preview_sql(
        "SELECT 1 AS value",
        user_context={"tenant_id": TENANT, "workspace_id": WORKSPACE},
    )

    assert result == {"error": "SQL blocked by table-function safety policy"}
    connection.execute.assert_not_called()
