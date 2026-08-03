from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest

from refinement.app import main as refinement_main
from refinement.app.duckdb_engine import DuckDBEngine
from refinement.app.staged_publication_engine import StagedPublicationEngine


TENANT = "tenant-a"
WORKSPACE = "workspace-a"
PAIR_KEY = "p0-console-refinement-pair-key-more-than-32-characters"
E_STRING_CANARY = "WITH e AS (SELECT 1) SELECT * FROM E'secret.csv'"
CTE_SCOPE_CANARY = (
    'WITH "/tmp/p0secret.csv" AS (SELECT * FROM "/tmp/p0secret.csv") '
    'SELECT * FROM "/tmp/p0secret.csv"'
)
QUALIFIED_SCAN_CANARY = 'SELECT * FROM foo."bar.csv"'
QUALIFIED_CTE_SCAN_CANARY = 'WITH "bar.csv" AS (SELECT 1) SELECT * FROM foo."bar.csv"'
MATERIALIZE_ESCAPE_CANARIES = (
    E_STRING_CANARY,
    CTE_SCOPE_CANARY,
    QUALIFIED_SCAN_CANARY,
    QUALIFIED_CTE_SCAN_CANARY,
)
SCOPED_URI = (
    "s3://lakehouse/raw/p0_probe/events/"
    f"tenant_id={TENANT}/workspace_id={WORKSPACE}/data.parquet"
)
PUBLISHED_GOLD_SQL = (
    "SELECT * FROM pggold.omega_publication_gold."
    '"run_0123456789abcdef0123456789abcdef"'
)


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
        "allowed_cartridges": ["p0_probe"],
        "allowed_prefixes": [],
    }


def _signed_tool_body(tool: str, args: dict) -> dict:
    return {
        "tool": tool,
        "args": args,
        "security_context": refinement_main._sign_security_context(_security_context()),
    }


async def _post_mcp(body: dict, *, raise_app_exceptions: bool = True) -> httpx.Response:
    transport = httpx.ASGITransport(
        app=refinement_main.app,
        raise_app_exceptions=raise_app_exceptions,
    )
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://refinement.test",
    ) as client:
        return await client.post(
            "/mcp/invoke",
            headers={"x-api-key": PAIR_KEY, "x-internal-service": "console"},
            json=body,
        )


def _engine_with_effective_sql(sql: str) -> tuple[DuckDBEngine, MagicMock]:
    engine = object.__new__(DuckDBEngine)
    engine._duckdb_lock = threading.RLock()
    connection = MagicMock()
    engine._conn = MagicMock(return_value=connection)
    engine._inject_bucket = lambda value: value
    engine._scope_storage_sql = lambda *_args: sql
    engine._validate_scoped_storage_sql = lambda *_args: None
    engine._inject_latest_date = lambda value, *_args: value
    engine._snapshot_path = lambda *_args: "s3://lakehouse/silver/p0_probe/data.parquet"
    engine._copy_to_parquet = MagicMock(
        side_effect=AssertionError("unsafe execution reached")
    )
    return engine, connection


@pytest.mark.parametrize("sql", MATERIALIZE_ESCAPE_CANARIES)
@pytest.mark.asyncio
async def test_mcp_http_blocks_escape_on_save_dataset(
    monkeypatch: pytest.MonkeyPatch,
    sql: str,
) -> None:
    saved = MagicMock()
    monkeypatch.setattr(
        refinement_main.store, "get_dataset", MagicMock(return_value=None)
    )
    monkeypatch.setattr(refinement_main.store, "save_dataset", saved)
    monkeypatch.setenv("INTERNAL_API_KEY_CONSOLE_TO_REFINEMENT", PAIR_KEY)
    body = _signed_tool_body(
        "save_dataset",
        {
            "name": "p0_probe",
            "sql": sql,
            "layer": "silver",
            "cartridge": "p0_probe",
            "sources": [],
        },
    )

    response = await _post_mcp(body)

    assert response.status_code == 403
    assert response.json() == {
        "detail": "SQL table function or storage path is not allowed"
    }
    saved.assert_not_called()


@pytest.mark.parametrize("sql", MATERIALIZE_ESCAPE_CANARIES)
@pytest.mark.asyncio
async def test_mcp_http_blocks_stored_escape_on_materialize(
    monkeypatch: pytest.MonkeyPatch,
    sql: str,
) -> None:
    dataset = {
        "name": "p0_probe",
        "sql_def": sql,
        "layer": "silver",
        "cartridge": "p0_probe",
        "sources": [],
        "tenant_id": TENANT,
        "workspace_id": WORKSPACE,
        "created_by_id": 41,
    }
    engine, connection = _engine_with_effective_sql(sql)
    monkeypatch.setattr(
        refinement_main.store, "get_dataset", MagicMock(return_value=dataset)
    )
    monkeypatch.setattr(refinement_main.engine, "materialize", engine.materialize)
    monkeypatch.setenv("INTERNAL_API_KEY_CONSOLE_TO_REFINEMENT", PAIR_KEY)

    response = await _post_mcp(
        _signed_tool_body("materialize", {"name": "p0_probe"}),
        raise_app_exceptions=False,
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "materialization_failed"
    assert detail["detail"] == "Error interno"
    assert "secret.csv" not in response.text
    assert "/tmp" not in response.text
    engine._conn.assert_not_called()
    connection.execute.assert_not_called()


@pytest.mark.parametrize("sql", MATERIALIZE_ESCAPE_CANARIES)
def test_materialize_revalidates_effective_sql_before_scope_probe(sql: str) -> None:
    engine, connection = _engine_with_effective_sql(sql)

    with pytest.raises(ValueError, match="safety policy") as exc:
        engine.materialize(
            {
                "name": "p0_probe",
                "sql_def": "SELECT 1 AS value",
                "layer": "silver",
                "cartridge": "p0_probe",
                "sources": [],
            },
            {"tenant_id": TENANT, "workspace_id": WORKSPACE},
        )

    assert str(exc.value) == "SQL blocked by safety policy"
    connection.execute.assert_not_called()


def test_server_resolved_literal_list_is_only_allowed_in_effective_sql() -> None:
    engine = object.__new__(DuckDBEngine)
    engine.minio_bucket = "lakehouse"
    sql = f"SELECT * FROM read_parquet(['{SCOPED_URI}'])"

    with pytest.raises(ValueError, match="safety policy"):
        engine._validate_safe_sql(sql)

    with pytest.raises(ValueError, match="safety policy"):
        engine._validate_effective_sql(sql)

    engine._validate_effective_sql(sql, allow_server_resolved_path_list=True)


def test_staged_binding_resolves_exact_list_and_preserves_scope_check() -> None:
    engine = object.__new__(StagedPublicationEngine)
    engine.minio_bucket = "lakehouse"
    engine.storage = SimpleNamespace(config=SimpleNamespace(provider="s3"))
    source = "raw/p0_probe/events"
    context = {"tenant_id": TENANT, "workspace_id": WORKSPACE}
    wildcard = engine._bronze_path(source, context)
    key = SCOPED_URI.removeprefix("s3://lakehouse/")
    engine._state = lambda: {
        "input_state": [{"source": source, "objects": [{"key": key}]}]
    }

    effective = engine._scope_storage_sql(
        f"SELECT * FROM read_parquet('{wildcard}')",
        [source],
        context,
    )

    assert effective == f"SELECT * FROM read_parquet(['{SCOPED_URI}'])"
    engine._validate_scoped_storage_sql(effective, context)
    engine._validate_effective_sql(effective, allow_server_resolved_path_list=True)
    foreign = effective.replace(f"workspace_id={WORKSPACE}", "workspace_id=workspace-b")
    with pytest.raises(ValueError, match="outside"):
        engine._validate_scoped_storage_sql(foreign, context)


@pytest.mark.parametrize("layer", ["silver", "gold"])
def test_materialize_marks_both_effective_gates_as_server_resolved(layer: str) -> None:
    class ValidationComplete(Exception):
        pass

    engine, _connection = _engine_with_effective_sql(PUBLISHED_GOLD_SQL)
    validate_effective = MagicMock(side_effect=[None, ValidationComplete])
    engine._validate_effective_sql = validate_effective
    engine._ensure_scope_columns = lambda _con, sql, _ctx: sql
    engine._pg_gold_attach = lambda *_args: None

    with pytest.raises(ValidationComplete):
        engine.materialize(
            {
                "name": "p0_probe",
                "sql_def": "SELECT 1 AS value",
                "layer": layer,
                "cartridge": "p0_probe",
                "sources": [],
            },
            {"tenant_id": TENANT, "workspace_id": WORKSPACE},
        )

    assert validate_effective.call_count == 2
    for call in validate_effective.call_args_list:
        assert call.kwargs == {
            "allow_server_resolved_path_list": True,
            "allow_server_resolved_publication_relation": True,
        }
