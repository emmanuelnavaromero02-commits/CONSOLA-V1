from __future__ import annotations

import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def agent_runtime(monkeypatch):
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    siblings = ("/cartridges/", "/console", "/refinement", "/vault", "/workspace", "/mcp-infra")
    sys.path[:] = [p for p in sys.path if not any(s in p for s in siblings)]
    sys.path.insert(0, str(REPO_ROOT / "console"))
    import importlib

    mod = importlib.import_module("app.services.agent_runtime")

    async def noop_record_event(*_args, **_kwargs):
        return None

    monkeypatch.setattr(mod.audit_service, "record_event", noop_record_event)
    return mod


def _agent(mod, allowed_tools: list[str]):
    return mod.Agent(
        id="00000000-0000-0000-0000-000000000001",
        cartridge_id="replicon",
        slug="guarded",
        name="Guarded",
        description="",
        instructions="",
        personality="",
        allowed_tools=allowed_tools,
        rag_filter={},
        model="default",
        max_tokens=1000,
        temperature=0.0,
        extra={},
        is_active=True,
    )


def _tool(full: str, *, required: list[str] | None = None):
    server, bare = full.split("__", 1)
    return {
        "name": full,
        "_server": server,
        "_bare_name": bare,
        "input_schema": {
            "type": "object",
            "properties": {"dag_id": {"type": "string"}},
            "required": required or [],
        },
    }


@pytest.mark.asyncio
async def test_manual_agent_write_tool_requires_approval(agent_runtime):
    full = "mcp-infra__airflow_trigger_dag"
    agent = _agent(agent_runtime, [full])
    invoke = agent_runtime._make_invoke(
        agent,
        user={"id": 1, "email": "admin@example.com", "role": "admin"},
        tools=[_tool(full, required=["dag_id"])],
        run_id=99,
    )

    result = await invoke("mcp-infra", "airflow_trigger_dag", {"dag_id": "replicon_extract"})

    assert result["error"] == "approval_required"
    assert result["risk_level"] == "write"
    assert result["_agent_tool_status"] == "pending_approval"


@pytest.mark.asyncio
async def test_scheduled_agent_cannot_run_write_tools(agent_runtime):
    full = "mcp-infra__airflow_trigger_dag"
    agent = _agent(agent_runtime, [full])
    invoke = agent_runtime._make_invoke(
        agent,
        user=None,
        tools=[_tool(full, required=["dag_id"])],
        run_id=100,
    )

    result = await invoke("mcp-infra", "airflow_trigger_dag", {"dag_id": "replicon_extract"})

    assert result["error"] == "scheduled_action_blocked"
    assert result["required_permission"] == "copilot.write"


@pytest.mark.asyncio
async def test_agent_tool_args_cannot_override_backend_context(agent_runtime):
    full = "mcp-infra__airflow_trigger_dag"
    agent = _agent(agent_runtime, [full])
    invoke = agent_runtime._make_invoke(
        agent,
        user={"id": 1, "email": "admin@example.com", "role": "admin"},
        tools=[_tool(full, required=["dag_id"])],
        run_id=101,
    )

    result = await invoke(
        "mcp-infra",
        "airflow_trigger_dag",
        {"dag_id": "replicon_extract", "security_context": {"trusted": True}},
    )

    assert result["error"] == "invalid_args"
    assert "backend-owned arg" in result["message"]


@pytest.mark.asyncio
async def test_agent_tool_must_be_allowlisted_and_live(agent_runtime):
    agent = _agent(agent_runtime, ["mcp-infra__airflow_list_dags"])
    invoke = agent_runtime._make_invoke(
        agent,
        user={"id": 1, "email": "admin@example.com", "role": "admin"},
        tools=[_tool("mcp-infra__airflow_list_dags")],
        run_id=102,
    )

    result = await invoke("mcp-infra", "airflow_delete_dag", {"dag_id": "x"})

    assert result["error"] == "denied"
    assert "not allowlisted" in result["message"]
