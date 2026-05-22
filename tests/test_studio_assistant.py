from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
CSRF = "csrf-test-token"


@pytest.fixture()
def studio_assistant_module():
    os.environ["APP_ENV"] = "test"
    os.environ["INTERNAL_API_KEY"] = "x" * 64
    console_dir = str(REPO / "console")
    sys.path[:] = [p for p in sys.path if p != console_dir]
    sys.path.insert(0, console_dir)
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    from app.services import studio_assistant

    return studio_assistant


def _servers():
    return [{
        "id": "infra",
        "name": "MCP Infra",
        "healthy": True,
        "tools": [
            {"name": "list_cartridges", "description": "List", "input_schema": {"type": "object"}},
            {"name": "airflow_list_dags", "description": "DAGs", "input_schema": {"type": "object"}},
            {"name": "superset_create_dataset", "description": "Superset", "input_schema": {"type": "object"}},
            {"name": "evil_delete_everything", "description": "Nope", "input_schema": {"type": "object"}},
        ],
    }]


@pytest.mark.asyncio
async def test_studio_assistant_uses_scoped_system_prompt(studio_assistant_module, monkeypatch):
    captured = {}

    async def fake_list_servers():
        return _servers()

    async def fake_chat(**kwargs):
        captured.update(kwargs)
        return "ok", [], kwargs["messages"]

    monkeypatch.setattr(studio_assistant_module.mcp_registry, "list_servers", fake_list_servers)
    monkeypatch.setattr(studio_assistant_module.llm_client, "chat", fake_chat)

    await studio_assistant_module.chat(
        message="ayuda",
        history=[],
        step=1,
        manifest={"id": "replicon", "name": "Replicon"},
        actor_role="admin",
    )

    assert "Tu ámbito es SOLO Studio" in captured["system"]
    assert "MODecissions Studio" in captured["system"]


@pytest.mark.asyncio
async def test_studio_assistant_only_exposes_whitelisted_tools(studio_assistant_module, monkeypatch):
    captured = {}

    async def fake_list_servers():
        return _servers()

    async def fake_chat(**kwargs):
        captured.update(kwargs)
        return "ok", [], kwargs["messages"]

    monkeypatch.setattr(studio_assistant_module.mcp_registry, "list_servers", fake_list_servers)
    monkeypatch.setattr(studio_assistant_module.llm_client, "chat", fake_chat)

    await studio_assistant_module.chat("lista", [], step=1, manifest={}, actor_role="admin")

    exposed = {tool["name"] for tool in captured["tools"]}
    assert "infra__list_cartridges" in exposed
    assert "infra__evil_delete_everything" not in exposed


@pytest.mark.asyncio
async def test_copilot_service_respects_tools_whitelist(studio_assistant_module):
    tools = [
        {"name": "infra__list_cartridges"},
        {"name": "infra__evil_delete_everything"},
    ]

    filtered = studio_assistant_module.filter_tools_by_whitelist(tools, {"list_cartridges"})

    assert filtered == [{"name": "infra__list_cartridges"}]


def test_studio_assistant_audits_messages(monkeypatch):
    os.environ["APP_ENV"] = "test"
    os.environ["INTERNAL_API_KEY"] = "x" * 64
    os.environ.setdefault("FIELD_ENCRYPTION_KEY", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa=")
    console_dir = str(REPO / "console")
    sys.path[:] = [p for p in sys.path if p != console_dir]
    sys.path.insert(0, console_dir)
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]

    from starlette.testclient import TestClient

    from app.dependencies import require_authenticated
    from app.main import app
    from app.routers import studio as studio_router

    admin = {"id": 1, "email": "admin@local.ai", "role": "admin", "workspace_role": "admin"}
    audits = []

    async def fake_chat(**_kwargs):
        return {"reply": "ok", "viewer_urls": [], "messages": []}

    async def fake_get_cartridge(_cartridge):
        return {"id": "replicon", "name": "Replicon"}

    async def fake_audit(**kwargs):
        audits.append(kwargs)

    app.dependency_overrides[require_authenticated] = lambda: admin
    app.dependency_overrides[studio_router.require_studio_write] = lambda: admin
    app.dependency_overrides[studio_router.require_studio_global_admin] = lambda: admin
    monkeypatch.setattr(studio_router.studio_assistant, "chat", fake_chat)
    monkeypatch.setattr(studio_router.cartridge_service, "get_cartridge", fake_get_cartridge)
    monkeypatch.setattr(studio_router.audit_service, "record_event", fake_audit)

    client = TestClient(app)
    client.cookies.set("csrf_token", CSRF)
    try:
        response = client.post(
            "/api/studio/assistant",
            json={"message": "ayuda", "cartridge": "replicon"},
            headers={"X-CSRF-Token": CSRF},
        )
    finally:
        app.dependency_overrides.pop(require_authenticated, None)
        app.dependency_overrides.pop(studio_router.require_studio_write, None)
        app.dependency_overrides.pop(studio_router.require_studio_global_admin, None)

    assert response.status_code == 200, response.text
    assert audits[0]["action"] == "studio.assistant.message"


def test_copilot_service_default_unchanged():
    source = (REPO / "console/app/services/copilot_service.py").read_text(encoding="utf-8")
    assert "async def run_turn(" in source
    assert "tools_whitelist" not in source
