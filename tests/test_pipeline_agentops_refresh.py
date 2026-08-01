from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.domains.pipeline.agentops_refresh import (
    run_sync_agentops_monitors,
    run_sync_agentops_status,
)
from app.services import sync_agentops


@pytest.mark.anyio
async def test_run_sync_agentops_monitors_returns_blocked_without_candidates():
    ensured = []
    list_calls = []

    async def ensure_monitor(user):
        ensured.append(user)

    async def list_agents(**kwargs):
        list_calls.append(kwargs)
        return []

    payload = await run_sync_agentops_monitors(
        cartridge="sap_successfactors",
        sync_run_id="sync-now-1",
        user={"sub": "user-1"},
        ensure_successfactors_talent_monitor=ensure_monitor,
        list_agents=list_agents,
        load_agent=None,
        reserve_scheduled_run=None,
        run_scheduled_monitor=None,
        finish_scheduled_run=None,
        sync_agentops_monitor_candidates=lambda agents: agents,
        sync_agentops=sync_agentops,
        logger_warning=None,
    )

    assert ensured == [{"sub": "user-1"}]
    assert list_calls == [
        {
            "cartridge_id": "sap_successfactors",
            "include_inactive": False,
            "user_context": {"sub": "user-1"},
        }
    ]
    assert payload["status"] == "partial"
    assert payload["total"] == 0


@pytest.mark.anyio
async def test_run_sync_agentops_monitors_executes_and_finishes_monitor():
    agent = SimpleNamespace(
        id="agent-1",
        slug="talent-monitor",
        tenant_id="tenant-1",
        workspace_id="workspace-1",
        cartridge_id="sap_successfactors",
    )
    reserve_calls = []
    run_calls = []
    finish_calls = []

    async def ensure_monitor(_user):
        return None

    async def list_agents(**_kwargs):
        return [{"id": "agent-1", "slug": "talent-monitor"}]

    async def load_agent(agent_id, **kwargs):
        assert agent_id == "agent-1"
        assert kwargs == {"user_context": {"sub": "user-1"}}
        return agent

    async def reserve_scheduled_run(**kwargs):
        reserve_calls.append(kwargs)
        return {"id": "reservation-1", "status": "reserved", "fencing_token": 1}

    async def run_scheduled_monitor(agent_arg, message, **kwargs):
        run_calls.append((agent_arg, message, kwargs))
        return {"run_id": "agent-run-1", "deterministic_monitor": True}

    async def finish_scheduled_run(**kwargs):
        finish_calls.append(kwargs)

    payload = await run_sync_agentops_monitors(
        cartridge="sap_successfactors",
        sync_run_id="sync-now-1",
        user={"sub": "user-1"},
        ensure_successfactors_talent_monitor=ensure_monitor,
        list_agents=list_agents,
        load_agent=load_agent,
        reserve_scheduled_run=reserve_scheduled_run,
        run_scheduled_monitor=run_scheduled_monitor,
        finish_scheduled_run=finish_scheduled_run,
        sync_agentops_monitor_candidates=lambda agents: agents,
        sync_agentops=sync_agentops,
        logger_warning=lambda *_args, **_kwargs: None,
    )

    assert payload["status"] == "success"
    assert payload["completed"] == 1
    assert payload["failed"] == 0
    assert reserve_calls[0]["agent_id"] == "agent-1"
    assert reserve_calls[0]["tenant_id"] == "tenant-1"
    assert reserve_calls[0]["workspace_id"] == "workspace-1"
    assert reserve_calls[0]["airflow_dag_run_id"] == "sync-now-1"
    assert run_calls[0][0] is agent
    assert "datos reales" in run_calls[0][1]
    assert finish_calls == [
        {
            "schedule_run_id": "reservation-1",
            "agent_run_id": "agent-run-1",
            "status": "ok",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "fencing_token": 1,
            "metadata": {
                "sync_run_id": "sync-now-1",
                "checked_at": payload["checked_at"],
                "deterministic_monitor": True,
            },
        }
    ]


@pytest.mark.anyio
async def test_run_sync_agentops_status_runs_successfactors_monitor_when_ready():
    calls = []

    async def run_monitors(**kwargs):
        calls.append(kwargs)
        return {
            "status": "success",
            "checked_at": "2026-07-03T00:00:00+00:00",
            "total": 1,
            "completed": 1,
            "failed": 0,
            "results": [{"status": "success"}],
        }

    status = await run_sync_agentops_status(
        cartridge="sap_successfactors",
        sync_run_id="sync-now-1",
        running_children=False,
        bronze_ready=1,
        silver_ready=0,
        gold_ready=0,
        control_room_update={"status": "partial"},
        agentops_refresh={},
        user={"sub": "user-1"},
        run_sync_agentops_monitors=run_monitors,
        sync_agentops_is_terminal=lambda payload: False,
        logger_warning=None,
    )

    assert calls == [
        {
            "cartridge": "sap_successfactors",
            "sync_run_id": "sync-now-1",
            "user": {"sub": "user-1"},
        }
    ]
    assert status["can_run_agentops"] is True
    assert status["agentops_refresh"]["status"] == "success"
    assert status["update"]["status"] == "success"


@pytest.mark.anyio
async def test_run_sync_agentops_status_reuses_terminal_payload():
    async def run_monitors(**_kwargs):
        raise AssertionError("terminal payload should not trigger a monitor")

    status = await run_sync_agentops_status(
        cartridge="sap_successfactors",
        sync_run_id="sync-now-1",
        running_children=False,
        bronze_ready=1,
        silver_ready=1,
        gold_ready=1,
        control_room_update={"status": "success"},
        agentops_refresh={"status": "success", "total": 1, "completed": 1},
        user=None,
        run_sync_agentops_monitors=run_monitors,
        sync_agentops_is_terminal=lambda payload: payload.get("status") == "success",
        logger_warning=None,
    )

    assert status["can_run_agentops"] is True
    assert status["agentops_refresh"]["status"] == "success"
    assert status["update"]["status"] == "success"


@pytest.mark.anyio
async def test_run_sync_agentops_status_captures_monitor_failure():
    warnings = []

    async def run_monitors(**_kwargs):
        raise RuntimeError("agent runner offline")

    status = await run_sync_agentops_status(
        cartridge="sap_successfactors",
        sync_run_id="sync-now-1",
        running_children=False,
        bronze_ready=1,
        silver_ready=0,
        gold_ready=0,
        control_room_update={"status": "partial"},
        agentops_refresh={},
        user=None,
        run_sync_agentops_monitors=run_monitors,
        sync_agentops_is_terminal=lambda payload: False,
        logger_warning=lambda *args, **kwargs: warnings.append((args, kwargs)),
    )

    assert warnings
    assert status["can_run_agentops"] is True
    assert status["agentops_refresh"]["status"] == "failed"
    assert "agent runner offline" in status["agentops_refresh"]["reason"]
    assert status["update"]["status"] == "failed"


@pytest.mark.anyio
async def test_run_sync_agentops_status_skips_non_successfactors_cartridge():
    async def run_monitors(**_kwargs):
        raise AssertionError("non SuccessFactors cartridges should not run here")

    status = await run_sync_agentops_status(
        cartridge="replicon",
        sync_run_id="sync-now-1",
        running_children=False,
        bronze_ready=1,
        silver_ready=1,
        gold_ready=1,
        control_room_update={"status": "success"},
        agentops_refresh={"status": "success"},
        user=None,
        run_sync_agentops_monitors=run_monitors,
        sync_agentops_is_terminal=lambda payload: True,
        logger_warning=None,
    )

    assert status["can_run_agentops"] is False
    assert status["agentops_refresh"] == {}
    assert status["update"]["status"] == "skipped"
