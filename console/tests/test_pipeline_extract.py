from __future__ import annotations

import importlib
import sys
import types

import pytest
from fastapi import HTTPException


INTERNAL_KEY = "test_internal_api_key_with_more_than_32_chars"


def _module(**attrs):
    mod = types.ModuleType("stub")
    for key, value in attrs.items():
        setattr(mod, key, value)
    return mod


async def _noop_async(*args, **kwargs):
    return None


@pytest.fixture()
def anyio_backend():
    return "asyncio"


@pytest.fixture()
def console_main(monkeypatch):
    monkeypatch.setenv("INTERNAL_API_KEY", INTERNAL_KEY)
    monkeypatch.setenv("JWT_SECRET_KEY", "test_jwt_secret_key_with_more_than_32_chars")
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")
    monkeypatch.setenv("ACCESS_TOKEN_EXPIRE_MINUTES", "15")

    auth_stub = _module(
        COOKIE_NAME="mod_session",
        REFRESH_COOKIE_NAME="refresh_token",
        close_pool=_noop_async,
        cookie_secure=lambda: False,
    )

    service_stubs = {
        "app.services.auth": auth_stub,
        "app.services.tokens": _module(close_pool=_noop_async),
        "app.services.email_service": _module(),
        "app.services.mcp_registry": _module(
            startup=_noop_async,
            health_check_all=_noop_async,
            close_pool=_noop_async,
        ),
        "app.services.assistant": _module(),
        "app.services.studio_assistant": _module(),
        "app.services.token_store": _module(close_pool=_noop_async),
        "app.services.job_service": _module(close_pool=_noop_async),
        "app.services.cartridge_service": _module(close_pool=_noop_async),
    }
    for name, mod in service_stubs.items():
        monkeypatch.setitem(sys.modules, name, mod)

    sys.modules.pop("app.main", None)
    sys.modules.pop("app.dependencies", None)
    main = importlib.import_module("app.main")
    yield main
    sys.modules.pop("app.main", None)
    sys.modules.pop("app.dependencies", None)


@pytest.mark.anyio
async def test_dag_based_cartridge_triggers_airflow_dag(console_main, monkeypatch):
    async def metadata(cartridge, entity):
        return {
            "pattern": "dag-based",
            "entity": entity,
            "dag_id": "replicon_extract",
            "mode": "full",
            "enabled": True,
            "primary_key": "department_id",
        }

    calls = []

    async def invoke(server, tool, args):
        calls.append((server, tool, args))
        return {"dag_run_id": "manual__test", "state": "queued"}

    monkeypatch.setattr(console_main, "_pipeline_extract_metadata", metadata)
    monkeypatch.setattr(console_main.mcp_registry, "invoke", invoke)

    result = await console_main.api_pipeline_extract("replicon", "Department", {})

    assert calls == [
        (
            "infra",
            "airflow_trigger_dag",
            {
                "dag_id": "replicon_extract",
                "conf": {"entity": "Department", "mode": "full"},
            },
        )
    ]
    assert result["triggered"] is True
    assert result["cartridge"] == "replicon"
    assert result["entity"] == "Department"
    assert result["dag_id"] == "replicon_extract"
    assert result["run_id"] == "manual__test"


@pytest.mark.anyio
async def test_dag_based_incremental_conf_preserves_dates(console_main, monkeypatch):
    async def metadata(cartridge, entity):
        return {
            "pattern": "dag-based",
            "entity": entity,
            "dag_id": "replicon_extract",
            "mode": "incremental",
            "enabled": True,
            "primary_key": "entry_id",
        }

    calls = []

    async def invoke(server, tool, args):
        calls.append((server, tool, args))
        return {"dag_run_id": "manual__incremental", "state": "queued"}

    monkeypatch.setattr(console_main, "_pipeline_extract_metadata", metadata)
    monkeypatch.setattr(console_main.mcp_registry, "invoke", invoke)

    await console_main.api_pipeline_extract(
        "replicon",
        "TimeEntry",
        {"mode": "incremental", "from_date": "2026-01-01", "to_date": "2026-01-31"},
    )

    assert calls[0][2]["conf"] == {
        "entity": "TimeEntry",
        "mode": "incremental",
        "from_date": "2026-01-01",
        "to_date": "2026-01-31",
    }


@pytest.mark.anyio
async def test_mcp_based_cartridge_keeps_mcp_invoke_fallback(console_main, monkeypatch):
    async def metadata(cartridge, entity):
        return {"pattern": "mcp", "entity": entity}

    calls = []

    async def invoke(server, tool, args):
        calls.append((server, tool, args))
        return {"job_id": "job-1"}

    monkeypatch.setattr(console_main, "_pipeline_extract_metadata", metadata)
    monkeypatch.setattr(console_main.mcp_registry, "invoke", invoke)

    result = await console_main.api_pipeline_extract("some_server", "Customer", {"mode": "full"})

    assert calls == [("some_server", "extract", {"entity": "Customer", "mode": "full"})]
    assert result == {"job_id": "job-1"}


@pytest.mark.anyio
async def test_dag_based_missing_entity_returns_404(console_main, monkeypatch):
    async def metadata(cartridge, entity):
        return {"pattern": "dag-based", "entity": None}

    monkeypatch.setattr(console_main, "_pipeline_extract_metadata", metadata)

    with pytest.raises(HTTPException) as exc:
        await console_main.api_pipeline_extract("replicon", "Missing", {})

    assert exc.value.status_code == 404


@pytest.mark.anyio
async def test_dag_based_disabled_entity_returns_400(console_main, monkeypatch):
    async def metadata(cartridge, entity):
        return {
            "pattern": "dag-based",
            "entity": entity,
            "dag_id": "replicon_extract",
            "mode": "full",
            "enabled": False,
        }

    monkeypatch.setattr(console_main, "_pipeline_extract_metadata", metadata)

    with pytest.raises(HTTPException) as exc:
        await console_main.api_pipeline_extract("replicon", "Department", {})

    assert exc.value.status_code == 400
