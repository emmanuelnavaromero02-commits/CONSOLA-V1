from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def agent_runtime(monkeypatch):
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    siblings = (
        "/cartridges/",
        "/console",
        "/refinement",
        "/vault",
        "/workspace",
        "/mcp-infra",
    )
    sys.path[:] = [p for p in sys.path if not any(s in p for s in siblings)]
    sys.path.insert(0, str(REPO_ROOT / "console"))
    import importlib

    mod = importlib.import_module("app.services.agent_runtime")

    async def noop_record_event(*_args, **_kwargs):
        return None

    async def noop_scheduled_audit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(mod.audit_service, "record_event", noop_record_event)
    monkeypatch.setattr(mod, "_record_scheduled_audit", noop_scheduled_audit)
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


def _monitor_agent(mod, allowed_tools: list[str]):
    agent = _agent(mod, allowed_tools)
    agent.extra = {"role": "monitor", "category": "control_room", "scope": "workspace"}
    agent.tenant_id = "11111111-1111-1111-1111-111111111111"
    agent.workspace_id = "22222222-2222-2222-2222-222222222222"
    return agent


def _scoped_agent(mod, allowed_tools: list[str]):
    agent = _agent(mod, allowed_tools)
    agent.tenant_id = "11111111-1111-1111-1111-111111111111"
    agent.workspace_id = "22222222-2222-2222-2222-222222222222"
    return agent


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
async def test_scheduled_monitor_defers_publication_without_calling_removed_alert_tool(
    agent_runtime, monkeypatch
):
    agent = _monitor_agent(
        agent_runtime,
        ["mcp-infra__wisdom_bits__run"],
    )
    agent.extra = {
        "role": "monitor",
        "monitor": {
            "wisdom_bit_id": "WB-SAFE",
            "threshold": {"min_signal_count": 1},
            "engines": [],
        },
    }
    calls: list[str] = []

    async def invoke(server_id: str, tool: str, _args: dict):
        calls.append(f"{server_id}__{tool}")
        return {
            "result": {
                "status": "ready",
                "data_sufficient": True,
                "tenant_id": agent.tenant_id,
                "workspace_id": agent.workspace_id,
                "signals": {"count": 1, "items": [{"signal": "ready"}]},
                "blockers": [],
            }
        }

    monkeypatch.setattr(
        agent_runtime,
        "_discover_agent_tools",
        AsyncMock(return_value=([], {})),
    )
    monkeypatch.setattr(agent_runtime, "_start_run", AsyncMock(return_value=501))
    finish = AsyncMock()
    monkeypatch.setattr(agent_runtime, "_finish_run", finish)
    monkeypatch.setattr(agent_runtime, "_make_invoke", lambda *_args, **_kwargs: invoke)

    result = await agent_runtime.run_scheduled_monitor(
        agent,
        "run",
        schedule_run_id=41,
        fencing_token=7,
    )

    assert calls == ["mcp-infra__wisdom_bits__run"]
    assert result["alert"] is None
    assert result["monitor"]["alerted"] is False
    assert result["monitor"]["grounded_handoff_required"] is True
    assert "alert=grounded_required" in result["reply"]
    finish.assert_awaited_once()
    assert finish.await_args.kwargs["status"] == "ok"


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

    result = await invoke(
        "mcp-infra", "airflow_trigger_dag", {"dag_id": "replicon_extract"}
    )

    assert result["error"] == "approval_required"
    assert result["risk_level"] == "write"
    assert result["_agent_tool_status"] == "pending_approval"


@pytest.mark.asyncio
async def test_talent_monitor_cannot_manually_orchestrate_decisions(
    agent_runtime, monkeypatch
):
    full = "mcp-infra__decision__orchestrate"
    agent = _monitor_agent(agent_runtime, [full])
    agent.cartridge_id = "sap_successfactors"
    agent.slug = "sap_successfactors_talent_monitor"
    captured: dict = {}
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("INTERNAL_API_KEY", "transport-key-" + "x" * 40)
    monkeypatch.setenv("SECURITY_CONTEXT_SIGNING_KEY", "signing-key-" + "y" * 40)

    class Response:
        status_code = 200
        text = "{}"

        def json(self):
            return {"result": {"ok": True}}

    class Client:
        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, _url, json):
            captured["payload"] = json
            return Response()

    monkeypatch.setattr(agent_runtime.httpx, "AsyncClient", Client)
    invoke = agent_runtime._make_invoke(
        agent,
        user={"id": 1, "email": "admin@example.com", "role": "admin"},
        tools=[
            {
                "name": full,
                "_server": "mcp-infra",
                "_bare_name": "decision__orchestrate",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "execute_engines": {"type": "boolean"},
                        "engine_inputs": {"type": "object"},
                    },
                },
            }
        ],
        run_id=105,
    )

    result = await invoke(
        "mcp-infra",
        "decision__orchestrate",
        {
            "execute_engines": True,
            "engine_inputs": {"monte_carlo": {"iterations": 1_000}},
        },
    )

    assert result["error"] == "denied"
    assert result["_agent_tool_status"] == "denied"
    assert result["message"] == (
        "manual Talent decision orchestration is paused by server policy"
    )
    assert captured == {}


@pytest.mark.asyncio
async def test_talent_monitor_cannot_publish_model_authored_alert_directly(
    agent_runtime,
):
    full = "mcp-infra__control_room__raise_analysis_alert"
    agent = _monitor_agent(agent_runtime, [full])
    agent.cartridge_id = "sap_successfactors"
    agent.slug = "sap_successfactors_talent_monitor"
    invoke = agent_runtime._make_invoke(
        agent,
        user={"id": 1, "email": "admin@example.com", "role": "admin"},
        tools=[
            {
                "name": full,
                "_server": "mcp-infra",
                "_bare_name": "control_room__raise_analysis_alert",
                "input_schema": {"type": "object", "properties": {}},
            }
        ],
        run_id=106,
    )

    result = await invoke(
        "mcp-infra",
        "control_room__raise_analysis_alert",
        {"message": "Inventé 99 personas sin evidencia"},
    )

    assert result["error"] == "grounded_handoff_required"
    assert "verified flow" in result["message"]


@pytest.mark.asyncio
async def test_scheduled_agent_cannot_run_write_tools(agent_runtime):
    full = "mcp-infra__airflow_trigger_dag"
    agent = _scoped_agent(agent_runtime, [full])
    invoke = agent_runtime._make_invoke(
        agent,
        user=None,
        tools=[_tool(full, required=["dag_id"])],
        run_id=100,
    )

    result = await invoke(
        "mcp-infra", "airflow_trigger_dag", {"dag_id": "replicon_extract"}
    )

    assert result["error"] == "scheduled_action_blocked"
    assert result["required_permission"] == "copilot.write"


@pytest.mark.asyncio
async def test_scheduled_agent_requires_workspace_scope(agent_runtime):
    full = "mcp-infra__airflow_list_dags"
    agent = _agent(agent_runtime, [full])
    invoke = agent_runtime._make_invoke(
        agent,
        user=None,
        tools=[_tool(full)],
        run_id=103,
    )

    result = await invoke("mcp-infra", "airflow_list_dags", {})

    assert result["error"] == "scope_required"


@pytest.mark.asyncio
async def test_scheduled_monitor_can_raise_advisory_control_room_alert(
    agent_runtime, monkeypatch
):
    full = "mcp-infra__control_room__raise_alert"
    agent = _monitor_agent(agent_runtime, [full])
    captured: dict = {}

    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("INTERNAL_API_KEY", "transport-key-" + "x" * 40)
    monkeypatch.setenv("SECURITY_CONTEXT_SIGNING_KEY", "signing-key-" + "y" * 40)

    async def capture_audit(_agent, _schedule_run_id, _fencing_token, **values):
        captured["audit"] = values

    monkeypatch.setattr(agent_runtime, "_record_scheduled_audit", capture_audit)

    class Response:
        status_code = 200
        text = "{}"

        def json(self):
            return {"result": {"ok": True, "item_id": "agent_alert:test"}}

    class Client:
        def __init__(self, *args, **kwargs):
            captured["headers"] = kwargs.get("headers")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, json):
            captured["url"] = url
            captured["payload"] = json
            return Response()

    monkeypatch.setattr(agent_runtime.httpx, "AsyncClient", Client)
    invoke = agent_runtime._make_invoke(
        agent,
        user=None,
        tools=[
            {
                "name": full,
                "_server": "mcp-infra",
                "_bare_name": "control_room__raise_alert",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "alert_type": {"type": "string"},
                        "cartridge_id": {"type": "string"},
                        "domain": {"type": "string"},
                        "source_dataset": {"type": "string"},
                        "entity_key": {"type": "string"},
                        "title": {"type": "string"},
                        "message": {"type": "string"},
                        "severity": {"type": "string"},
                        "confidence": {"type": "number"},
                    },
                    "required": [
                        "alert_type",
                        "cartridge_id",
                        "domain",
                        "source_dataset",
                        "entity_key",
                        "title",
                        "message",
                        "severity",
                        "confidence",
                    ],
                },
            }
        ],
        run_id=104,
        schedule_run_id=7,
        fencing_token=1,
    )

    result = await invoke(
        "mcp-infra",
        "control_room__raise_alert",
        {
            "alert_type": "margin_watch",
            "cartridge_id": "replicon",
            "domain": "Finanzas",
            "source_dataset": "gold_pnl_mensual",
            "entity_key": "client=acme/month=2026-06",
            "title": "Margen fuera de rango",
            "message": "El monitor detecto margen bajo.",
            "severity": "high",
            "confidence": 0.82,
        },
    )

    assert result["ok"] is True
    payload = captured["payload"]
    ctx = payload["security_context"]
    assert payload["tool"] == "control_room__raise_alert"
    assert ctx["tenant_id"] == agent.tenant_id
    assert ctx["workspace_id"] == agent.workspace_id
    assert ctx["agent_id"] == agent.id
    assert "control_room.write" in ctx["permissions"]
    assert "control_room.execute" not in ctx["permissions"]
    assert "_signature" in ctx
    assert "effect_authority" not in captured["audit"]["tool_args"]
    assert "action_handle" not in captured["audit"]["tool_args"]


@pytest.mark.asyncio
async def test_scheduled_monitor_can_read_bayesian_calibration_state(
    agent_runtime, monkeypatch
):
    full = "mcp-infra__calibration__bayesian_state"
    agent = _monitor_agent(agent_runtime, [full])
    captured: dict = {}

    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("INTERNAL_API_KEY", "transport-key-" + "x" * 40)
    monkeypatch.setenv("SECURITY_CONTEXT_SIGNING_KEY", "signing-key-" + "y" * 40)

    class Response:
        status_code = 200
        text = "{}"

        def json(self):
            return {
                "result": {
                    "ok": True,
                    "engine": "bayesian_calibration",
                    "state_count": 1,
                }
            }

    class Client:
        def __init__(self, *args, **kwargs):
            captured["headers"] = kwargs.get("headers")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, json):
            captured["url"] = url
            captured["payload"] = json
            return Response()

    monkeypatch.setattr(agent_runtime.httpx, "AsyncClient", Client)
    invoke = agent_runtime._make_invoke(
        agent,
        user=None,
        tools=[
            {
                "name": full,
                "_server": "mcp-infra",
                "_bare_name": "calibration__bayesian_state",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "calibration_group": {"type": "string"},
                        "model_version": {"type": "string"},
                        "limit": {"type": "integer"},
                    },
                },
            }
        ],
        run_id=106,
    )

    result = await invoke(
        "mcp-infra",
        "calibration__bayesian_state",
        {
            "calibration_group": "sap_successfactors:talent_readiness",
            "model_version": "bayesian_calibration.v1",
            "limit": 10,
        },
    )

    assert result["ok"] is True
    payload = captured["payload"]
    ctx = payload["security_context"]
    assert payload["tool"] == "calibration__bayesian_state"
    assert ctx["tenant_id"] == agent.tenant_id
    assert ctx["workspace_id"] == agent.workspace_id
    assert ctx["agent_id"] == agent.id
    assert "datasets.read" in ctx["permissions"]
    assert "_signature" in ctx


@pytest.mark.asyncio
async def test_agent_runtime_accepts_legacy_infra_alias_for_mcp_infra(
    agent_runtime, monkeypatch
):
    allowed = "infra__control_room__raise_alert"
    canonical = "mcp-infra__control_room__raise_alert"
    agent = _monitor_agent(agent_runtime, [allowed])
    captured: dict = {}

    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("INTERNAL_API_KEY", "transport-key-" + "x" * 40)
    monkeypatch.setenv("SECURITY_CONTEXT_SIGNING_KEY", "signing-key-" + "y" * 40)

    class Response:
        status_code = 200
        text = "{}"

        def json(self):
            return {"result": {"ok": True, "item_id": "agent_alert:legacy"}}

    class Client:
        def __init__(self, *args, **kwargs):
            captured["headers"] = kwargs.get("headers")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, json):
            captured["url"] = url
            captured["payload"] = json
            return Response()

    monkeypatch.setattr(agent_runtime.httpx, "AsyncClient", Client)
    invoke = agent_runtime._make_invoke(
        agent,
        user=None,
        tools=[
            {
                "name": allowed,
                "_server": "infra",
                "_bare_name": "control_room__raise_alert",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "alert_type": {"type": "string"},
                        "cartridge_id": {"type": "string"},
                        "domain": {"type": "string"},
                        "source_dataset": {"type": "string"},
                        "entity_key": {"type": "string"},
                        "title": {"type": "string"},
                        "message": {"type": "string"},
                        "severity": {"type": "string"},
                        "confidence": {"type": "number"},
                    },
                    "required": [
                        "alert_type",
                        "cartridge_id",
                        "domain",
                        "source_dataset",
                        "entity_key",
                        "title",
                        "message",
                        "severity",
                        "confidence",
                    ],
                },
            }
        ],
        run_id=105,
        schedule_run_id=8,
        fencing_token=1,
    )

    assert canonical in agent_runtime._tool_lookup([{"name": allowed}])
    result = await invoke(
        "mcp-infra",
        "control_room__raise_alert",
        {
            "alert_type": "talent_watch",
            "cartridge_id": "sap_successfactors",
            "domain": "Recursos Humanos",
            "source_dataset": "sap_successfactors_talent_signals",
            "entity_key": "WB-TALENTO",
            "title": "Monitor activo",
            "message": "El monitor detecto señales agregadas.",
            "severity": "medium",
            "confidence": 0.8,
        },
    )

    assert result["ok"] is True
    assert captured["payload"]["tool"] == "control_room__raise_alert"
    assert captured["payload"]["security_context"]["agent_id"] == agent.id


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


@pytest.mark.asyncio
async def test_rag_filter_cartridge_is_server_enforced(agent_runtime, monkeypatch):
    full = "mcp-infra__search_rag"
    agent = _scoped_agent(agent_runtime, [full])
    agent.rag_filter = {
        "cartridges": ["sap_successfactors"],
        "kinds": ["document", "schema"],
    }
    captured: dict = {}

    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("INTERNAL_API_KEY", "transport-key-" + "x" * 40)
    monkeypatch.setenv("SECURITY_CONTEXT_SIGNING_KEY", "signing-key-" + "y" * 40)

    class Response:
        status_code = 200
        text = "{}"

        def json(self):
            return {"result": {"results": []}}

    class Client:
        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, _url, json):
            captured["payload"] = json
            return Response()

    monkeypatch.setattr(agent_runtime.httpx, "AsyncClient", Client)
    invoke = agent_runtime._make_invoke(
        agent,
        user=None,
        tools=[
            {
                "name": full,
                "_server": "mcp-infra",
                "_bare_name": "search_rag",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "kinds": {"type": "array", "items": {"type": "string"}},
                        "cartridges": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["query"],
                },
            }
        ],
        run_id=109,
    )

    result = await invoke("mcp-infra", "search_rag", {"query": "readiness"})

    assert result == {"results": []}
    assert captured["payload"]["args"]["cartridges"] == ["sap_successfactors"]
    assert captured["payload"]["args"]["kinds"] == ["document", "schema"]


@pytest.mark.asyncio
async def test_rag_filter_rejects_out_of_scope_cartridge(agent_runtime):
    full = "mcp-infra__search_rag"
    agent = _scoped_agent(agent_runtime, [full])
    agent.rag_filter = {"cartridges": ["sap_successfactors"]}
    invoke = agent_runtime._make_invoke(
        agent,
        user=None,
        tools=[
            {
                "name": full,
                "_server": "mcp-infra",
                "_bare_name": "search_rag",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "cartridges": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["query"],
                },
            }
        ],
        run_id=110,
    )

    result = await invoke(
        "mcp-infra",
        "search_rag",
        {"query": "finance", "cartridges": ["sap_s4hana"]},
    )

    assert result["error"] == "scope_denied"
