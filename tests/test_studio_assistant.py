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


def _refinement_servers():
    return [{
        "id": "refinement",
        "name": "Refinement",
        "healthy": True,
        "tools": [
            {"name": "preview_transform", "description": "Preview", "input_schema": {"type": "object"}},
            {"name": "save_dataset", "description": "Save", "input_schema": {"type": "object"}},
        ],
    }]


def _monitoring_servers():
    return [{
        "id": "monitoring",
        "name": "Monitoring & Deeplinks",
        "healthy": True,
        "tools": [
            {"name": "view_job", "description": "Job link", "input_schema": {"type": "object"}},
            {"name": "view_pipeline", "description": "Pipeline link", "input_schema": {"type": "object"}},
            {"name": "view_semantic", "description": "Semantic link", "input_schema": {"type": "object"}},
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
    assert "ΩMEGA Studio" in captured["system"]


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
async def test_studio_assistant_exposes_monitoring_deeplinks_in_entities_step(studio_assistant_module, monkeypatch):
    captured = {}

    async def fake_list_servers():
        return _monitoring_servers()

    async def fake_chat(**kwargs):
        captured.update(kwargs)
        return "ok", [], kwargs["messages"]

    monkeypatch.setattr(studio_assistant_module.mcp_registry, "list_servers", fake_list_servers)
    monkeypatch.setattr(studio_assistant_module.llm_client, "chat", fake_chat)

    await studio_assistant_module.chat(
        "sincroniza y muéstrame avance",
        [],
        step=3,
        manifest={"id": "sap_successfactors", "name": "SAP SuccessFactors"},
        actor_role="admin",
    )

    exposed = {tool["name"] for tool in captured["tools"]}
    assert "monitoring__view_job" in exposed
    assert "monitoring__view_pipeline" in exposed
    assert "monitoring__view_semantic" in exposed


@pytest.mark.asyncio
async def test_studio_assistant_blocks_non_admin_direct_write_tool_invocation(studio_assistant_module, monkeypatch):
    captured = {}
    audits = []
    invoked = False

    async def fake_list_servers():
        return _servers()

    async def fake_invoke(*_args, **_kwargs):
        nonlocal invoked
        invoked = True
        raise AssertionError("direct write tool should not be invoked")

    async def fake_audit(**kwargs):
        audits.append(kwargs)

    async def fake_chat(**kwargs):
        result = await kwargs["invoke_tool"](
            "infra",
            "superset_create_dataset",
            {"dataset": "gold_margin", "password": "secret"},
        )
        captured["result"] = result
        return "ok", [], kwargs["messages"]

    monkeypatch.setattr(studio_assistant_module.mcp_registry, "list_servers", fake_list_servers)
    monkeypatch.setattr(studio_assistant_module.mcp_registry, "invoke", fake_invoke)
    monkeypatch.setattr(studio_assistant_module.audit_service, "record_event", fake_audit)
    monkeypatch.setattr(studio_assistant_module.llm_client, "chat", fake_chat)

    await studio_assistant_module.chat(
        "crea este dataset",
        [],
        step=5,
        manifest={"id": "hubspot", "name": "HubSpot"},
        actor_role="operator",
        actor_user={"id": 7, "email": "operator@local.ai"},
    )

    assert invoked is False
    assert captured["result"]["approval_required"] is True
    assert captured["result"]["tool"] == "infra__superset_create_dataset"
    assert captured["result"]["args_preview"]["password"] == "***"
    assert audits[-1]["status"] == "pending_approval"
    assert audits[-1]["tool_result_status"] == "pending_approval"


@pytest.mark.asyncio
async def test_studio_admin_can_run_internal_write_tool_directly(studio_assistant_module, monkeypatch):
    captured = {}
    audits = []

    async def fake_list_servers():
        return _servers()

    async def fake_invoke(_server, tool, args, **_kwargs):
        return {"ok": True, "tool": tool, "dataset": args["dataset"]}

    async def fake_audit(**kwargs):
        audits.append(kwargs)

    async def fake_chat(**kwargs):
        captured["result"] = await kwargs["invoke_tool"](
            "infra",
            "superset_create_dataset",
            {"dataset": "gold_margin", "password": "secret"},
        )
        return "ok", [], kwargs["messages"]

    monkeypatch.setattr(studio_assistant_module.mcp_registry, "list_servers", fake_list_servers)
    monkeypatch.setattr(studio_assistant_module.mcp_registry, "invoke", fake_invoke)
    monkeypatch.setattr(studio_assistant_module.audit_service, "record_event", fake_audit)
    monkeypatch.setattr(studio_assistant_module.llm_client, "chat", fake_chat)

    await studio_assistant_module.chat(
        "crea este dataset",
        [],
        step=5,
        manifest={"id": "hubspot", "name": "HubSpot"},
        actor_role="admin",
        actor_user={"id": 7, "email": "admin@local.ai", "role": "admin"},
    )

    assert captured["result"] == {
        "ok": True,
        "tool": "superset_create_dataset",
        "dataset": "gold_margin",
    }
    assert audits[-1]["status"] == "success"


@pytest.mark.asyncio
async def test_studio_admin_destructive_tool_still_requires_approval(studio_assistant_module, monkeypatch):
    captured = {}
    audits = []

    async def fake_list_servers():
        return [{
            "id": "infra",
            "name": "MCP Infra",
            "healthy": True,
            "tools": [
                {"name": "postgres_execute_ddl", "description": "DDL", "input_schema": {"type": "object"}},
            ],
        }]

    async def fake_invoke(*_args, **_kwargs):
        raise AssertionError("destructive tool should not be invoked directly")

    async def fake_audit(**kwargs):
        audits.append(kwargs)

    async def fake_chat(**kwargs):
        captured["result"] = await kwargs["invoke_tool"](
            "infra",
            "postgres_execute_ddl",
            {"sql": "DROP TABLE gold_margin"},
        )
        return "ok", [], kwargs["messages"]

    monkeypatch.setattr(studio_assistant_module.mcp_registry, "list_servers", fake_list_servers)
    monkeypatch.setattr(studio_assistant_module.mcp_registry, "invoke", fake_invoke)
    monkeypatch.setattr(studio_assistant_module.audit_service, "record_event", fake_audit)
    monkeypatch.setattr(studio_assistant_module.llm_client, "chat", fake_chat)

    await studio_assistant_module.chat(
        "ejecuta este DDL",
        [],
        step=4,
        manifest={"id": "hubspot", "name": "HubSpot"},
        actor_role="admin",
        actor_user={"id": 7, "email": "admin@local.ai", "role": "admin"},
    )

    assert captured["result"]["approval_required"] is True
    assert captured["result"]["tool"] == "infra__postgres_execute_ddl"
    assert audits[-1]["status"] == "pending_approval"


@pytest.mark.asyncio
async def test_refine_step_blocks_save_after_failed_preview(studio_assistant_module, monkeypatch):
    captured = {}
    invocations = []

    async def fake_list_servers():
        return _refinement_servers()

    async def fake_invoke(_srv, tool, args, **_kwargs):
        invocations.append((tool, args))
        if tool == "preview_transform":
            return {"error": "SQL storage path not allowed", "status_code": 403}
        raise AssertionError("save_dataset must not be invoked after a failed preview")

    async def fake_audit(**_kwargs):
        return None

    async def fake_chat(**kwargs):
        await kwargs["invoke_tool"](
            "refinement",
            "preview_transform",
            {"sql": "SELECT * FROM missing", "limit": 20},
        )
        captured["save_result"] = await kwargs["invoke_tool"](
            "refinement",
            "save_dataset",
            {"name": "bad_gold", "sql": "SELECT * FROM missing", "layer": "gold"},
        )
        return "ok", [], kwargs["messages"]

    monkeypatch.setattr(studio_assistant_module.mcp_registry, "list_servers", fake_list_servers)
    monkeypatch.setattr(studio_assistant_module.mcp_registry, "invoke", fake_invoke)
    monkeypatch.setattr(studio_assistant_module.audit_service, "record_event", fake_audit)
    monkeypatch.setattr(studio_assistant_module.llm_client, "chat", fake_chat)

    await studio_assistant_module.chat(
        "crea un gold",
        [],
        step=4,
        manifest={"id": "sap_successfactors", "name": "SAP SuccessFactors"},
        actor_role="admin",
        actor_user={"id": 7, "email": "admin@local.ai"},
    )

    assert [tool for tool, _args in invocations] == ["preview_transform"]
    assert "preview_transform exitoso" in captured["save_result"]["error"]


@pytest.mark.asyncio
async def test_refine_step_super_admin_can_save_after_successful_preview_without_approval_key(studio_assistant_module, monkeypatch):
    captured = {}
    invocations = []

    async def fake_list_servers():
        return _refinement_servers()

    async def fake_invoke(_srv, tool, args, **_kwargs):
        invocations.append((tool, args))
        if tool == "preview_transform":
            return {"row_count": 1, "rows": [{"ok": 1}]}
        if tool == "save_dataset":
            return {"saved": True, "name": args["name"]}
        raise AssertionError(f"unexpected tool {tool}")

    async def fake_audit(**_kwargs):
        return None

    async def fake_chat(**kwargs):
        await kwargs["invoke_tool"](
            "refinement",
            "preview_transform",
            {"sql": "SELECT 1 AS ok", "limit": 20},
        )
        captured["save_result"] = await kwargs["invoke_tool"](
            "refinement",
            "save_dataset",
            {"name": "good_gold", "sql": "SELECT 1 AS ok", "layer": "gold"},
        )
        return "ok", [], kwargs["messages"]

    monkeypatch.setattr(studio_assistant_module.mcp_registry, "list_servers", fake_list_servers)
    monkeypatch.setattr(studio_assistant_module.mcp_registry, "invoke", fake_invoke)
    monkeypatch.setattr(studio_assistant_module.audit_service, "record_event", fake_audit)
    monkeypatch.setattr(studio_assistant_module.llm_client, "chat", fake_chat)

    await studio_assistant_module.chat(
        "crea un gold",
        [],
        step=4,
        manifest={"id": "sap_successfactors", "name": "SAP SuccessFactors"},
        actor_role="admin",
        actor_user={"id": 7, "email": "admin@local.ai", "role": "admin"},
    )

    assert [tool for tool, _args in invocations] == ["preview_transform", "save_dataset"]
    assert captured["save_result"] == {"saved": True, "name": "good_gold"}
    assert "approval_key" not in captured["save_result"]


@pytest.mark.asyncio
async def test_copilot_service_respects_tools_whitelist(studio_assistant_module):
    tools = [
        {"name": "infra__list_cartridges"},
        {"name": "infra__evil_delete_everything"},
    ]

    filtered = studio_assistant_module.filter_tools_by_whitelist(tools, {"list_cartridges"})

    assert filtered == [{"name": "infra__list_cartridges"}]


def test_studio_assistant_analyst_policy_uses_manifest_risk(studio_assistant_module):
    assert studio_assistant_module.is_tool_allowed_for_role("analyst", "infra__list_cartridges") is True
    assert studio_assistant_module.is_tool_allowed_for_role("analyst", "infra__dag_get_source") is False
    assert studio_assistant_module.is_tool_allowed_for_role("analyst", "infra__cartridge_query_kb") is False


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
