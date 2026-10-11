from __future__ import annotations

import importlib
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

_AIRFLOW_KEY = "airflow-to-console-test-key-with-sufficient-entropy"
_WORKSPACE_KEY = "workspace-to-console-test-key-with-sufficient-entropy"
_RUNNER = "runner-token-with-sufficient-entropy"
TENANT = "11111111-1111-4111-8111-111111111111"
WORKSPACE = "22222222-2222-4222-8222-222222222222"
OTHER = "33333333-3333-4333-8333-333333333333"
AGENT = "44444444-4444-4444-8444-444444444444"
REFRESH = "dataset_refresh_chain:successfactors__dataset_refresh__abc"
_SCOPE = {"tenant_id": TENANT, "workspace_id": WORKSPACE, "refresh_run_id": REFRESH}


def _load_console_modules():
    console_root = Path(__file__).resolve().parents[1]
    sibling_roots = ("/cartridges/", "/refinement", "/vault", "/workspace")
    sys.path[:] = [
        path
        for path in sys.path
        if path != str(console_root) and not any(marker in path for marker in sibling_roots)
    ]
    sys.path.insert(0, str(console_root))
    for module_name in list(sys.modules):
        if module_name == "app" or module_name.startswith("app."):
            del sys.modules[module_name]
    main = importlib.import_module("app.main")
    service = importlib.import_module("app.services.scheduled_automation")
    return main, service


def _app_modules() -> dict:
    return {
        name: module
        for name, module in sys.modules.items()
        if name == "app" or name.startswith("app.")
    }


@pytest.fixture()
def automation_http(monkeypatch: pytest.MonkeyPatch):
    saved_modules = _app_modules()
    saved_path = list(sys.path)
    main, service = _load_console_modules()
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("INTERNAL_API_KEY_AIRFLOW_TO_CONSOLE", _AIRFLOW_KEY)
    monkeypatch.setenv("INTERNAL_API_KEY_WORKSPACE_TO_CONSOLE", _WORKSPACE_KEY)
    monkeypatch.setenv("AGENT_RUNNER_TOKEN", _RUNNER)
    pool = object()
    mocks = {
        "targets": AsyncMock(return_value={"status": "ready", "targets": [], "workspaces": 0, "failures": []}),
        "due": AsyncMock(return_value={"status": "no_monitors", "due": []}),
        "invoke": AsyncMock(return_value={"ok": True, "status": "completed"}),
    }
    monkeypatch.setattr(service, "successfactors_targets", mocks["targets"])
    monkeypatch.setattr(service, "refresh_monitors_due", mocks["due"])
    monkeypatch.setattr(service, "invoke_refresh_monitor", mocks["invoke"])
    router = importlib.import_module("app.routers.scheduled_automation")
    monkeypatch.setattr(router.auth, "pool", AsyncMock(return_value=pool))
    yield TestClient(main.app, raise_server_exceptions=False), mocks
    for name in _app_modules():
        del sys.modules[name]
    sys.modules.update(saved_modules)
    sys.path[:] = saved_path


_AIRFLOW = {"X-Api-Key": _AIRFLOW_KEY, "X-Internal-Service": "airflow"}


def test_airflow_reaches_every_automation_route(automation_http):
    client, mocks = automation_http
    runner = {**_AIRFLOW, "X-Agent-Runner-Token": _RUNNER}

    assert client.post("/internal/automation/successfactors/daily-targets", headers=_AIRFLOW, json={}).status_code == 200
    assert client.post("/internal/automation/refresh-monitors/due", headers=runner, json=_SCOPE).status_code == 200
    response = client.post(
        "/internal/automation/refresh-monitors/invoke",
        headers=runner,
        json={**_SCOPE, "agent_id": AGENT, "airflow_dag_run_id": "successfactors__dataset_refresh__abc"},
    )
    assert response.status_code == 200, response.text
    assert mocks["invoke"].await_args.kwargs["agent_id"] == AGENT
    assert mocks["due"].await_args.kwargs["refresh_run_id"] == REFRESH


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"X-Internal-Service": "airflow"},
        {"X-Api-Key": "wrong", "X-Internal-Service": "airflow"},
        {"X-Api-Key": _WORKSPACE_KEY, "X-Internal-Service": "workspace", "X-Agent-Runner-Token": _RUNNER},
    ],
    ids=("anonymous", "missing-key", "wrong-key", "other-service"),
)
def test_only_airflow_can_call_automation_routes(automation_http, headers):
    client, mocks = automation_http
    for path, body in (
        ("/internal/automation/successfactors/daily-targets", {}),
        ("/internal/automation/refresh-monitors/due", _SCOPE),
        ("/internal/automation/refresh-monitors/invoke", {**_SCOPE, "agent_id": AGENT}),
    ):
        assert client.post(path, headers=headers, json=body).status_code in {401, 403}
    for mock in mocks.values():
        mock.assert_not_awaited()


def test_monitor_routes_require_the_runner_token(automation_http):
    client, mocks = automation_http
    for token in (None, "wrong-token"):
        headers = dict(_AIRFLOW)
        if token:
            headers["X-Agent-Runner-Token"] = token
        assert client.post("/internal/automation/refresh-monitors/due", headers=headers, json=_SCOPE).status_code == 401
        assert (
            client.post(
                "/internal/automation/refresh-monitors/invoke",
                headers=headers,
                json={**_SCOPE, "agent_id": AGENT},
            ).status_code
            == 401
        )
    mocks["due"].assert_not_awaited()
    mocks["invoke"].assert_not_awaited()


def test_malformed_scope_is_rejected(automation_http):
    client, mocks = automation_http
    runner = {**_AIRFLOW, "X-Agent-Runner-Token": _RUNNER}
    for body in (
        {**_SCOPE, "workspace_id": "not-a-uuid"},
        {**_SCOPE, "refresh_run_id": "a b"},
        {**_SCOPE, "extra": 1},
    ):
        assert client.post("/internal/automation/refresh-monitors/due", headers=runner, json=body).status_code == 422
    mocks["due"].assert_not_awaited()


class _Conn:
    def __init__(self, *, row=None, rows=None):
        self.row = row
        self.rows = rows or []

    async def execute(self, *_args):
        return "SELECT 1"

    async def fetchrow(self, *_args):
        return self.row

    async def fetch(self, *_args):
        return self.rows


@pytest.fixture()
def service():
    from app.services import scheduled_automation

    return scheduled_automation


def _refresh_row(status: str = "success", age: timedelta = timedelta(minutes=5)) -> dict:
    return {
        "run_id": REFRESH,
        "cartridge_id": "sap_successfactors",
        "status": status,
        "finished_at": datetime.now(timezone.utc) - age,
    }


def test_successfactors_targets_use_the_first_enabled_connection_per_workspace(service, monkeypatch):
    scopes = [
        {"tenant_id": TENANT, "workspace_id": WORKSPACE},
        {"tenant_id": TENANT, "workspace_id": OTHER},
        {"tenant_id": TENANT, "workspace_id": AGENT},
    ]
    monkeypatch.setattr(service, "active_workspaces", AsyncMock(return_value=scopes))

    async def load(scope, *, cartridge_id):
        assert cartridge_id == "sap_successfactors"
        if scope["workspace_id"] == WORKSPACE:
            return {"connections": [{"conn_id": "off", "enabled": False}, {"conn_id": "tenant_sf"}]}
        if scope["workspace_id"] == OTHER:
            return None
        raise RuntimeError("vault unavailable")

    import asyncio

    result = asyncio.run(service.successfactors_targets(object(), load_connections=load))

    assert result["targets"] == [{"tenant_id": TENANT, "workspace_id": WORKSPACE, "conn_id": "tenant_sf"}]
    assert result["status"] == "partial"
    assert result["failures"] == [{"workspace_id": AGENT, "error_code": "RuntimeError"}]


@pytest.mark.parametrize(
    "row",
    [None, _refresh_row("failed"), _refresh_row("blocked"), _refresh_row(age=timedelta(days=2))],
)
def test_monitors_only_follow_a_recent_published_refresh(service, row):
    import asyncio

    with pytest.raises(HTTPException) as error:
        asyncio.run(
            service.recorded_refresh(
                _Conn(row=row), tenant_id=TENANT, workspace_id=WORKSPACE, refresh_run_id=REFRESH
            )
        )
    assert error.value.status_code == 409


def test_due_monitors_are_the_workspace_monitors_of_the_refreshed_source(service):
    import asyncio

    monitor = {"role": "monitor", "monitor": {"wisdom_bit_id": "WB-TALENTO"}, "schedule": {"cron": "0 7 * * *"}}
    conn = _Conn(
        row=_refresh_row("partial"),
        rows=[
            {"id": AGENT, "slug": "talent_monitor", "extra": monitor},
            {"id": OTHER, "slug": "advisor", "extra": {"role": "advisor"}},
            {"id": WORKSPACE, "slug": "paused", "extra": {**monitor, "schedule": {"enabled": False}}},
        ],
    )
    ensure = AsyncMock()

    result = asyncio.run(
        service.refresh_monitors_due(
            conn,
            tenant_id=TENANT,
            workspace_id=WORKSPACE,
            refresh_run_id=REFRESH,
            ensure_monitors=ensure,
        )
    )

    assert result["due"] == [{"id": AGENT, "slug": "talent_monitor"}]
    ensure.assert_awaited_once_with(TENANT, WORKSPACE, "sap_successfactors")


def _agent(**overrides):
    values = {
        "id": AGENT,
        "slug": "talent_monitor",
        "cartridge_id": "sap_successfactors",
        "tenant_id": TENANT,
        "workspace_id": WORKSPACE,
        "is_active": True,
        "extra": {"role": "monitor", "monitor": {"wisdom_bit_id": "WB-TALENTO"}},
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _invoke(service, agent, *, reservation=None, result=None):
    import asyncio

    reserve = AsyncMock(
        return_value=reservation
        or {"reserved": True, "duplicate": False, "id": 5, "status": "running", "fencing_token": 1}
    )
    execute = AsyncMock(
        return_value=result
        or {
            "run_id": 77,
            "deterministic_monitor": True,
            "monitor": {"status": "ok", "signals": 1, "blockers": 0, "engines": [], "alerted": True},
        }
    )
    payload = asyncio.run(
        service.invoke_refresh_monitor(
            _Conn(row=_refresh_row()),
            agent_id=AGENT,
            tenant_id=TENANT,
            workspace_id=WORKSPACE,
            refresh_run_id=REFRESH,
            airflow_dag_run_id="successfactors__dataset_refresh__abc",
            load_agent=AsyncMock(return_value=agent),
            reserve_scheduled_run=reserve,
            execute_reserved_scheduled_monitor=execute,
        )
    )
    return payload, reserve, execute


@pytest.mark.parametrize(
    "agent",
    [
        _agent(workspace_id=OTHER),
        _agent(cartridge_id="sap_b1"),
        _agent(extra={"role": "advisor"}),
        _agent(is_active=False),
        _agent(extra={"role": "monitor", "monitor": {"x": 1}, "schedule": {"enabled": False}}),
    ],
    ids=("other-workspace", "other-source", "not-monitor", "inactive", "disabled"),
)
def test_invoke_refuses_agents_outside_the_refresh(service, agent):
    with pytest.raises(HTTPException) as error:
        _invoke(service, agent)
    assert error.value.status_code == 403


def test_invoke_reserves_once_per_refresh_and_reports_like_a_scheduled_run(service):
    payload, reserve, execute = _invoke(service, _agent())

    kwargs = reserve.await_args.kwargs
    assert kwargs["scheduled_fire_at"] == service.REFRESH_FIRE_AT
    assert kwargs["schedule_key"] == service.refresh_schedule_key(REFRESH)
    assert kwargs["metadata"]["source"] == "after-refresh"
    assert payload["ok"] is True and payload["status"] == "completed"
    assert payload["duplicate"] is False
    assert payload["schedule_run"]["status"] == "ok" and payload["schedule_run"]["agent_run_id"] == 77
    execute.assert_awaited_once()


def test_duplicate_reservation_does_not_run_the_monitor_again(service):
    payload, _reserve, execute = _invoke(
        service,
        _agent(),
        reservation={"reserved": False, "duplicate": True, "id": 5, "status": "ok", "agent_run_id": 77, "fencing_token": 2},
    )
    assert payload["duplicate"] is True and payload["ok"] is True
    execute.assert_not_awaited()


def test_schedule_keys_differ_per_refresh_run(service):
    assert service.refresh_schedule_key(REFRESH) != service.refresh_schedule_key(REFRESH + "x")
    assert service.refresh_schedule_key(REFRESH).startswith("after-refresh:")
