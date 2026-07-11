from __future__ import annotations

import importlib
import json
import os

import pytest


@pytest.mark.asyncio
async def test_studio_assistant_exposes_goal_run_tools(monkeypatch):
    os.environ["APP_ENV"] = "test"
    importlib.import_module("app.routers.studio")
    studio_assistant = importlib.import_module("app.services.studio_assistant")
    captured = {}

    async def list_servers():
        return []

    async def chat(**kwargs):
        captured["tools"] = kwargs["tools"]
        captured["system"] = kwargs["system"]
        captured["messages"] = kwargs["messages"]
        return "ok", [], kwargs["messages"]

    monkeypatch.setattr(studio_assistant.mcp_registry, "list_servers", list_servers)
    monkeypatch.setattr(studio_assistant.llm_client, "chat", chat)

    await studio_assistant.chat(
        "valida este cartucho para producción",
        [],
        step=1,
        manifest={"id": "hubspot", "name": "HubSpot", "entities": [], "dags": []},
        actor_role="admin",
        actor_user={"id": 7, "email": "admin@local.ai", "workspace_role": "admin"},
    )

    tool_names = {tool["name"] for tool in captured["tools"]}
    assert "studio__cartridge_self_check" in tool_names
    assert "studio__create_goal_run" in tool_names
    assert "studio__execute_goal_run" in tool_names
    assert "studio__approve_goal_step" in tool_names
    assert "goal run" in captured["system"].lower()
    assert "approval_required" in captured["system"]


@pytest.mark.asyncio
async def test_studio_assistant_exposes_create_entity_in_entities_step(monkeypatch):
    os.environ["APP_ENV"] = "test"
    importlib.import_module("app.routers.studio")
    studio_assistant = importlib.import_module("app.services.studio_assistant")
    captured = {}

    async def list_servers():
        return []

    async def chat(**kwargs):
        captured["tools"] = kwargs["tools"]
        return "ok", [], kwargs["messages"]

    monkeypatch.setattr(studio_assistant.mcp_registry, "list_servers", list_servers)
    monkeypatch.setattr(studio_assistant.llm_client, "chat", chat)

    await studio_assistant.chat(
        "crea una entidad Invoice",
        [],
        step=3,
        manifest={"id": "hubspot", "name": "HubSpot", "entities": [], "dags": []},
        actor_role="admin",
        actor_user={"id": 7, "email": "admin@local.ai", "workspace_role": "admin"},
    )

    tool_names = {tool["name"] for tool in captured["tools"]}
    assert "studio__create_entity" in tool_names


@pytest.mark.asyncio
async def test_admin_can_create_entity_from_explicit_studio_request(monkeypatch):
    os.environ["APP_ENV"] = "test"
    studio_router = importlib.import_module("app.routers.studio")
    studio_assistant = importlib.import_module("app.services.studio_assistant")
    captured = {}

    async def list_servers():
        return []

    async def fake_create_entity(name, cartridge, spec, user):
        captured["create_call"] = {
            "name": name,
            "cartridge": cartridge,
            "spec": spec,
            "user": user,
        }
        return {"id": "ent-1", "name": name, "cartridge": cartridge, "spec": spec}

    async def record_event(**_kwargs):
        return None

    async def chat(**kwargs):
        result = await kwargs["invoke_tool"](
            "studio",
            "create_entity",
            {
                "cartridge_id": "hubspot",
                "entity": "Invoice",
                "fields": [{"name": "id", "type": "string", "primary_key": True}],
            },
        )
        captured["result"] = result
        return "ok", [], kwargs["messages"]

    monkeypatch.setattr(studio_assistant.mcp_registry, "list_servers", list_servers)
    monkeypatch.setattr(studio_assistant.llm_client, "chat", chat)
    monkeypatch.setattr(studio_assistant.audit_service, "record_event", record_event)
    monkeypatch.setattr(studio_router, "_require_cartridge_visible", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(studio_router.studio_entities, "create_entity", fake_create_entity)

    await studio_assistant.chat(
        "crea una entidad Invoice con id como primary key",
        [],
        step=3,
        manifest={"id": "hubspot", "name": "HubSpot", "entities": [], "dags": []},
        actor_role="admin",
        actor_user={"id": 7, "email": "admin@local.ai", "workspace_role": "admin"},
    )

    assert captured["result"]["created"] is True
    assert captured["result"]["entity"] == "Invoice"
    assert captured["create_call"]["spec"]["fields"][0]["name"] == "id"
    assert "approval_required" not in captured["result"]


@pytest.mark.asyncio
async def test_create_entity_without_explicit_request_still_requires_approval(monkeypatch):
    os.environ["APP_ENV"] = "test"
    importlib.import_module("app.routers.studio")
    studio_assistant = importlib.import_module("app.services.studio_assistant")
    captured = {}

    async def list_servers():
        return []

    async def record_event(**_kwargs):
        return None

    async def chat(**kwargs):
        captured["result"] = await kwargs["invoke_tool"](
            "studio",
            "create_entity",
            {
                "cartridge_id": "hubspot",
                "entity": "Invoice",
                "fields": [{"name": "id", "type": "string", "primary_key": True}],
            },
        )
        return "ok", [], kwargs["messages"]

    monkeypatch.setattr(studio_assistant.mcp_registry, "list_servers", list_servers)
    monkeypatch.setattr(studio_assistant.llm_client, "chat", chat)
    monkeypatch.setattr(studio_assistant.audit_service, "record_event", record_event)

    await studio_assistant.chat(
        "analiza el cartucho",
        [],
        step=3,
        manifest={"id": "hubspot", "name": "HubSpot", "entities": [], "dags": []},
        actor_role="admin",
        actor_user={"id": 7, "email": "admin@local.ai", "workspace_role": "admin"},
    )

    assert captured["result"]["approval_required"] is True
    assert captured["result"]["tool"] == "studio__create_entity"


@pytest.mark.asyncio
async def test_studio_assistant_reports_missing_llm_key_without_500(monkeypatch):
    monkeypatch.setenv("CHAT_LLM_PROVIDER", "anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    studio_assistant = importlib.import_module("app.services.studio_assistant")

    async def list_servers():
        return []

    monkeypatch.setattr(studio_assistant.mcp_registry, "list_servers", list_servers)

    result = await studio_assistant.chat(
        "hola",
        [],
        step=1,
        manifest={"id": "hubspot", "name": "HubSpot"},
        actor_role="admin",
        actor_user={"id": "u1", "email": "u@example.com", "role": "admin", "workspace_role": "admin"},
    )

    assert "ANTHROPIC_API_KEY is required" in result["reply"]
    assert result["viewer_urls"] == []


@pytest.mark.asyncio
async def test_studio_assistant_reports_provider_error_without_500(monkeypatch):
    studio_assistant = importlib.import_module("app.services.studio_assistant")

    async def list_servers():
        return []

    async def broken_chat(**_kwargs):
        raise studio_assistant.llm_client.LLMProviderError(
            "Anthropic authentication failed; verify ANTHROPIC_API_KEY"
        )

    monkeypatch.setattr(studio_assistant.mcp_registry, "list_servers", list_servers)
    monkeypatch.setattr(studio_assistant.llm_client, "chat", broken_chat)

    result = await studio_assistant.chat(
        "hola",
        [],
        step=1,
        manifest={"id": "hubspot", "name": "HubSpot"},
        actor_role="analyst",
        actor_user={"id": "u1", "email": "u@example.com", "workspace_role": "analyst"},
    )

    assert "proveedor LLM respondió con error" in result["reply"]
    assert "ANTHROPIC_API_KEY" in result["reply"]


def test_approval_tool_requires_explicit_current_user_message():
    studio_assistant = importlib.import_module("app.services.studio_assistant")

    assert not studio_assistant._current_user_text_allows_approval_tool(
        "approve_goal_step",
        {"approval_key": "11111111-1111-1111-1111-111111111111"},
        "valida HubSpot para producción",
    )
    assert not studio_assistant._current_user_text_allows_approval_tool(
        "approve_goal_step",
        {"approval_key": "11111111-1111-1111-1111-111111111111"},
        "apruebo este paso",
    )
    assert studio_assistant._current_user_text_allows_approval_tool(
        "approve_goal_step",
        {"approval_key": "11111111-1111-1111-1111-111111111111"},
        "apruebo 11111111-1111-1111-1111-111111111111",
    )
    assert not studio_assistant._current_user_text_allows_approval_tool(
        "approve_goal_step",
        {"approval_key": "11111111-1111-1111-1111-111111111111"},
        "no apruebo 11111111-1111-1111-1111-111111111111",
    )
    assert studio_assistant._current_user_text_allows_approval_tool(
        "reject_goal_step",
        {"approval_key": "11111111-1111-1111-1111-111111111111"},
        "rechazo 11111111-1111-1111-1111-111111111111",
    )


@pytest.mark.asyncio
async def test_refine_admin_save_dataset_bypasses_goal_approval_after_preview(monkeypatch):
    studio_assistant = importlib.import_module("app.services.studio_assistant")

    async def list_servers():
        return [{
            "id": "refinement",
            "name": "Refinement",
            "healthy": True,
            "tools": [
                {"name": "preview_transform", "description": "", "input_schema": {"type": "object"}},
                {"name": "save_dataset", "description": "", "input_schema": {"type": "object"}},
            ],
        }]

    async def invoke(server_id, tool, args, user=None):
        if server_id == "refinement" and tool == "preview_transform":
            return {"row_count": 1, "data": [{"ok": 1}]}
        if server_id == "refinement" and tool == "save_dataset":
            return {"saved": True, "name": args["name"], "user": (user or {}).get("email")}
        return {"error": "unexpected tool"}

    async def record_event(**_kwargs):
        return None

    async def chat(**kwargs):
        preview = await kwargs["invoke_tool"](
            "refinement",
            "preview_transform",
            {"sql": "SELECT 1 AS ok", "limit": 20},
        )
        saved = await kwargs["invoke_tool"](
            "refinement",
            "save_dataset",
            {
                "name": "sap_successfactors_workforce_composition",
                "sql": "SELECT 1 AS ok",
                "layer": "gold",
                "sources": ["sap_successfactors_employee_360"],
                "cartridge": "sap_successfactors",
            },
        )
        return json.dumps({"preview": preview, "saved": saved}), [], kwargs["messages"]

    monkeypatch.setattr(studio_assistant.mcp_registry, "list_servers", list_servers)
    monkeypatch.setattr(studio_assistant.mcp_registry, "invoke", invoke)
    monkeypatch.setattr(studio_assistant.audit_service, "record_event", record_event)
    monkeypatch.setattr(studio_assistant.llm_client, "chat", chat)

    result = await studio_assistant.chat(
        "genera un gold y guárdalo tras preview",
        [],
        step=4,
        manifest={"id": "sap_successfactors", "name": "SAP SuccessFactors"},
        actor_role="super_admin",
        actor_user={
            "id": 7,
            "email": "admin@local.ai",
            "role": "super_admin",
            "workspace_role": "super_admin",
        },
        tools_whitelist={"preview_transform", "save_dataset"},
    )

    payload = json.loads(result["reply"])
    assert payload["preview"]["row_count"] == 1
    assert payload["saved"] == {
        "saved": True,
        "name": "sap_successfactors_workforce_composition",
        "user": "admin@local.ai",
    }
    assert "approval_required" not in result["reply"]
    assert "approval_key" not in result["reply"]
