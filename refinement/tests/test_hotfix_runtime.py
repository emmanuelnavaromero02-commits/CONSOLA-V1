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

    monkeypatch.setitem(sys.modules, "app.llm_sql", _module(generate_sql=generate_sql))
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
