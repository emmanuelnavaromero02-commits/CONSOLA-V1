from __future__ import annotations

import threading
from unittest.mock import MagicMock

import httpx
import pytest

from refinement.app import main as refinement_main
from refinement.app.duckdb_engine import DuckDBEngine


TENANT = "tenant-a"
WORKSPACE = "workspace-a"
PAIR_KEY = "p0-console-refinement-pair-key-more-than-32-characters"
E_STRING_CANARY = "WITH e AS (SELECT 1) SELECT * FROM E'secret.csv'"


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
        "security_context": refinement_main._sign_security_context(
            _security_context()
        ),
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


@pytest.mark.asyncio
async def test_mcp_http_blocks_e_string_on_save_dataset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved = MagicMock()
    monkeypatch.setattr(refinement_main.store, "get_dataset", MagicMock(return_value=None))
    monkeypatch.setattr(refinement_main.store, "save_dataset", saved)
    monkeypatch.setenv("INTERNAL_API_KEY_CONSOLE_TO_REFINEMENT", PAIR_KEY)
    body = _signed_tool_body(
        "save_dataset",
        {
            "name": "p0_probe",
            "sql": E_STRING_CANARY,
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


@pytest.mark.asyncio
async def test_mcp_http_blocks_stored_e_string_on_materialize(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset = {
        "name": "p0_probe",
        "sql_def": E_STRING_CANARY,
        "layer": "silver",
        "cartridge": "p0_probe",
        "sources": [],
        "tenant_id": TENANT,
        "workspace_id": WORKSPACE,
        "created_by_id": 41,
    }
    engine, connection = _engine_with_effective_sql(E_STRING_CANARY)
    monkeypatch.setattr(refinement_main.store, "get_dataset", MagicMock(return_value=dataset))
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
    engine._conn.assert_not_called()
    connection.execute.assert_not_called()


def test_materialize_revalidates_effective_sql_before_scope_probe() -> None:
    engine, connection = _engine_with_effective_sql(E_STRING_CANARY)

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
