from __future__ import annotations

import asyncio
import json
import os
import sys
from contextlib import asynccontextmanager
from importlib import import_module
from pathlib import Path

import httpx
import pytest
from mcp import ClientSession
from mcp.client import streamable_http


REPO = Path(__file__).resolve().parents[1]
WORKSPACE_ID = "11111111-1111-1111-1111-111111111111"
TENANT_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
BASE = "http://testserver"


def _load_console(monkeypatch):
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    sys.path.insert(0, str(REPO / "console"))
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("INTERNAL_API_KEY", "sdk-conformance-internal-key-0123456789abcdef")
    monkeypatch.setenv("JWT_SECRET_KEY", "sdk-conformance-jwt-secret-0123456789abcdef")
    monkeypatch.setenv("APP_BASE_URL", "https://consola.example.test")
    monkeypatch.delenv("IA_GATEWAY_ENABLED", raising=False)
    main = import_module("app.main")
    deps = import_module("app.dependencies")
    access_tokens = import_module("app.services.access_tokens")
    audit_service = import_module("app.services.audit_service")
    adapters = import_module("app.services.mcp_gateway.adapters")
    dispatcher = import_module("app.services.mcp_gateway.dispatcher")
    token = access_tokens.generate_token()

    async def resolve(candidate, ip):
        if candidate != token:
            return None
        return {
            "token_id": "5d0f3c9e-7a51-4b1e-9d1c-0c9a6f6f2a11",  # gitleaks:allow
            "status": "activo",
            "user_id": 42,
            "email": "ana@example.invalid",
            "name": "Ana",
            "role": "user",
            "user_tenant_id": TENANT_ID,
            "tenant_id": TENANT_ID,
            "workspace_id": WORKSPACE_ID,
            "workspace_name": "Operaciones",
            "token_name": "Asistente",
            "token_prefix": token[:14],
            "scopes": ["acciones", "lectura"],
            "expires_at": "2026-12-31T00:00:00+00:00",
        }

    async def options(user):
        return [
            {
                "workspace_id": WORKSPACE_ID,
                "workspace_name": "Operaciones",
                "tenant_id": TENANT_ID,
                "tenant_name": "Empresa",
                "workspace_role": "workspace_admin",
            }
        ]

    async def cartridges(workspace_id, user_id=None):
        return ["sap_b1"]

    async def record_event(**kwargs):
        return None

    async def start_sync(user, *, cartridge, request_id):
        await asyncio.sleep(0.05)
        return {"run_id": "sync_now:sap_b1:r1", "status": "running", "active": True, "progress_percent": 10}

    async def stored_origin(user, *, cartridge, run_id):
        return None

    monkeypatch.setattr(access_tokens, "resolve_token", resolve)
    monkeypatch.setattr(deps, "_workspace_access_options", options)
    monkeypatch.setattr(deps, "_workspace_cartridges", cartridges)
    monkeypatch.setattr(audit_service, "record_event", record_event)
    monkeypatch.setattr(adapters, "start_sync", start_sync)
    monkeypatch.setattr(adapters, "sync_run_origin", stored_origin)
    monkeypatch.setattr(dispatcher, "KEEPALIVE_SECONDS", 0.01)
    return main, token


@asynccontextmanager
async def _connect(url: str, client: httpx.AsyncClient):
    modern = getattr(streamable_http, "streamable_http_client", None)
    if modern is not None:
        async with modern(url, http_client=client) as streams:
            yield streams
        return

    def factory(headers=None, timeout=None, auth=None):
        return client

    async with streamable_http.streamablehttp_client(url, httpx_client_factory=factory) as streams:
        yield streams


def _client(main, token: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main.app),
        base_url=BASE,
        headers={"Authorization": f"Bearer {token}"},
        timeout=httpx.Timeout(30.0),
    )


@pytest.mark.asyncio
async def test_official_client_initializes_lists_and_calls_tools(monkeypatch):
    main, token = _load_console(monkeypatch)
    async with _client(main, token) as client:
        async with _connect(f"{BASE}/api/ia/v1/mcp", client) as (read, write, get_session_id):
            async with ClientSession(read, write) as session:
                initialized = await session.initialize()
                assert initialized.serverInfo.name == "omega"
                assert initialized.capabilities.tools is not None
                assert get_session_id() is None
                listed = await session.list_tools()
                names = {tool.name for tool in listed.tools}
                assert {"consultar_contexto", "consultar_kpis_sap_b1", "ejecutar_extraccion"} <= names
                assert "consultar_matriz_talento_9box" not in names
                context = next(tool for tool in listed.tools if tool.name == "consultar_contexto")
                assert context.annotations.readOnlyHint is True
                result = await session.call_tool("consultar_contexto", {})
                assert result.isError is False
                assert result.structuredContent["fuentes_habilitadas"] == ["SAP Business One"]
                long_running = await session.call_tool("ejecutar_extraccion", {"fuente": "sap b1"})
                assert long_running.isError is False
                assert long_running.structuredContent["ejecucion_id"] == "sync_now:sap_b1:r1"
                denied = await session.call_tool("consultar_matriz_talento_9box", {})
                assert denied.isError is True
                assert json.loads(denied.content[0].text)["error"]["codigo"] == "fuente_no_habilitada"


@pytest.mark.asyncio
async def test_official_client_is_refused_without_a_personal_token(monkeypatch):
    main, _token = _load_console(monkeypatch)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url=BASE) as client:
        response = await client.post(
            "/api/ia/v1/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            headers={"Accept": "application/json, text/event-stream"},
        )
    assert response.status_code == 401
    assert response.json()["error"]["codigo"] == "token_invalido"


def test_openapi_document_is_valid_openapi_3_1(monkeypatch):
    from openapi_pydantic.v3.v3_1 import OpenAPI

    main, _token = _load_console(monkeypatch)
    from starlette.testclient import TestClient

    doc = TestClient(main.app).get("/api/ia/v1/openapi.json").json()
    parsed = OpenAPI.model_validate(doc)
    assert parsed.openapi == "3.1.0"
    assert doc["servers"][0]["url"] == "https://consola.example.test/api/ia/v1"
    read_only = TestClient(main.app).get("/api/ia/v1/openapi.json", params={"alcance": "lectura"}).json()
    OpenAPI.model_validate(read_only)
    assert os.environ.get("APP_ENV") == "test"
