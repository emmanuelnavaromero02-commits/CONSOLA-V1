from __future__ import annotations

import importlib
import sys
import types

import pytest
from fastapi import HTTPException


def _module(**attrs):
    mod = types.ModuleType("stub")
    for key, value in attrs.items():
        setattr(mod, key, value)
    return mod


class _GeneratedSQLValidationError(ValueError):
    pass


@pytest.fixture()
def anyio_backend():
    return "asyncio"


@pytest.fixture()
def refinement_main(monkeypatch):
    monkeypatch.setenv("INTERNAL_API_KEY", "test_internal_api_key_with_more_than_32_chars")
    monkeypatch.setitem(sys.modules, "app.duckdb_engine", _module(DuckDBEngine=lambda: object()))
    monkeypatch.setitem(sys.modules, "app.dataset_store", _module(DatasetStore=lambda path: object()))

    async def generate_sql(*args, **kwargs):
        return "", ""

    monkeypatch.setitem(
        sys.modules,
        "app.llm_sql",
        _module(
            GeneratedSQLValidationError=_GeneratedSQLValidationError,
            generate_sql=generate_sql,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "app.security",
        _module(get_internal_api_key=lambda: "test_internal_api_key_with_more_than_32_chars"),
    )
    sys.modules.pop("app.main", None)
    main = importlib.import_module("app.main")
    yield main
    sys.modules.pop("app.main", None)


def test_postgres_dsn_normalizes_sqlalchemy_driver(refinement_main, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg2://user:pass@host/db")

    assert refinement_main._postgres_dsn() == "postgresql://user:pass@host/db"


def test_postgres_dsn_keeps_native_postgres_url(refinement_main, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@host/db")

    assert refinement_main._postgres_dsn() == "postgresql://user:pass@host/db"


def test_dataset_allowed_filters_owned_datasets_for_workspace_employees(refinement_main):
    base_sec = {
        "trusted": True,
        "source": "console",
        "role": "user",
        "workspace_role": "viewer",
        "user_id": 10,
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "allowed_cartridges": ["hubspot"],
    }
    own_dataset = {
        "name": "forecast_mensual",
        "layer": "gold",
        "cartridge": "hubspot",
        "workspace_id": "workspace-a",
        "created_by_id": 10,
    }
    other_employee_dataset = {**own_dataset, "name": "deals_estancados", "created_by_id": 11}
    legacy_workspace_dataset = {**own_dataset, "name": "legacy_shared", "created_by_id": None}
    workspace_admin_sec = {**base_sec, "workspace_role": "tenant_admin", "user_id": 99}
    wildcard_admin_sec = {**workspace_admin_sec, "allowed_cartridges": ["*"]}

    assert refinement_main._dataset_allowed(base_sec, own_dataset)
    assert not refinement_main._dataset_allowed(base_sec, other_employee_dataset)
    assert refinement_main._dataset_allowed(base_sec, legacy_workspace_dataset)
    assert refinement_main._dataset_allowed(workspace_admin_sec, other_employee_dataset)
    assert refinement_main._dataset_allowed(wildcard_admin_sec, other_employee_dataset)


@pytest.mark.anyio
async def test_delete_dataset_rejects_invalid_name(refinement_main):
    with pytest.raises(HTTPException) as exc:
        await refinement_main.mcp_invoke({"tool": "delete_dataset", "args": {"name": "bad-name"}})

    assert exc.value.status_code == 400
    assert exc.value.detail == "Invalid dataset name"


@pytest.mark.anyio
async def test_describe_silver_rejects_invalid_name_before_path_build(refinement_main):
    with pytest.raises(HTTPException) as exc:
        await refinement_main.mcp_invoke({"tool": "describe_silver", "args": {"name": "../secret"}})

    assert exc.value.status_code == 400
    assert exc.value.detail == "Invalid dataset name"


@pytest.mark.anyio
async def test_rest_dataset_data_endpoint_is_disabled(refinement_main):
    """Regression: GET /datasets/{name}/data used to call query_dataset
    without a user context. Any peer holding INTERNAL_API_KEY (workspace,
    mcp-infra, replicon, airflow) could trigger an RLS-less read of any
    dataset whose SQL did not reference pggold.* . The endpoint is now
    disabled in favour of POST /mcp/invoke with a forwarded user_context."""
    with pytest.raises(HTTPException) as exc:
        await refinement_main.dataset_data("gold_sales", limit=10)

    assert exc.value.status_code == 410
    assert "user_context" in exc.value.detail.lower()
