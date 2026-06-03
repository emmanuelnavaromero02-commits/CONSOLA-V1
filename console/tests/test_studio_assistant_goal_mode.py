from __future__ import annotations

import importlib
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
