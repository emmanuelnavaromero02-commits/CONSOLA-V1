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
