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
    real_scheduled_audit = mod._record_scheduled_audit

    async def noop_record_event(*_args, **_kwargs):
        return None

    async def noop_scheduled_audit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(mod.audit_service, "record_event", noop_record_event)
    monkeypatch.setattr(mod, "_record_scheduled_audit", noop_scheduled_audit)
    mod._real_scheduled_audit_for_test = real_scheduled_audit
    return mod


@pytest.mark.asyncio
async def test_scheduled_tool_audit_forces_one_critical_scoped_write(
    agent_runtime, monkeypatch
):
    events: list[tuple] = []

    class Transaction:
        async def __aenter__(self):
            events.append(("transaction_enter",))
            return self

        async def __aexit__(self, *_args):
            events.append(("transaction_exit",))
            return None

    class Connection:
        def transaction(self):
            return Transaction()

        async def execute(self, query, *args):
            operation = (
                "effect_fence"
                if "assert_scheduled_effect_authority" in query
                else "set_scope"
            )
            events.append((operation, args))
            return "SELECT 1"

    connection = Connection()

    class Acquire:
        async def __aenter__(self):
            return connection

        async def __aexit__(self, *_args):
            return None

    class Pool:
        def acquire(self):
            return Acquire()

    async def get_pool():
        return Pool()

    async def record_event(**values):
        events.append(("audit", values))

    monkeypatch.setattr(agent_runtime, "_get_pool", get_pool)
    monkeypatch.setattr(agent_runtime.audit_service, "record_event", record_event)
    agent = _scoped_agent(agent_runtime, [])

    await agent_runtime._real_scheduled_audit_for_test(
        agent,
        17,
        9,
        action="agent.tool.test",
        critical=False,
        connection=object(),
    )

    event_names = [event[0] for event in events]
    assert event_names.index("set_scope") < event_names.index("effect_fence")
    assert event_names.index("effect_fence") < event_names.index("audit")
    audit_values = next(event[1] for event in events if event[0] == "audit")
    assert audit_values["critical"] is True
    assert audit_values["connection"] is connection
    assert audit_values["action"] == "agent.tool.test"

    async def fail_record_event(**_values):
        raise RuntimeError("critical audit unavailable")

    monkeypatch.setattr(agent_runtime.audit_service, "record_event", fail_record_event)
    with pytest.raises(RuntimeError, match="critical audit unavailable"):
        await agent_runtime._real_scheduled_audit_for_test(
            agent,
            17,
            9,
            action="agent.tool.test",
            critical=True,
        )


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
