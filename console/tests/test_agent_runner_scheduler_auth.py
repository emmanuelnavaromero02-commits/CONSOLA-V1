from __future__ import annotations

import importlib
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

_PATH = "/api/operations/internal/agent-runner/due"
_AIRFLOW_KEY = "airflow-to-console-test-key-with-sufficient-entropy"
_WORKSPACE_KEY = "workspace-to-console-test-key-with-sufficient-entropy"
_WINDOW = {
    "window_start": "2026-09-02T12:00:00Z",
    "window_end": "2026-09-02T12:05:00Z",
}


def _load_console_modules():
    console_root = Path(__file__).resolve().parents[1]
    sibling_roots = ("/cartridges/", "/refinement", "/vault", "/workspace")
    sys.path[:] = [
        path
        for path in sys.path
        if path != str(console_root)
        and not any(marker in path for marker in sibling_roots)
    ]
    sys.path.insert(0, str(console_root))
    for module_name in list(sys.modules):
        if module_name == "app" or module_name.startswith("app."):
            del sys.modules[module_name]
    main = importlib.import_module("app.main")
    operations = importlib.import_module("app.routers.operations")
    return main, operations


@pytest.fixture()
def scheduler_http(monkeypatch: pytest.MonkeyPatch):
    main, operations = _load_console_modules()

    # Exercise the production key policy: the legacy shared key must not be a
    # hidden fallback for this Airflow-only route.
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("INTERNAL_API_KEY_AIRFLOW_TO_CONSOLE", _AIRFLOW_KEY)
    monkeypatch.setenv("INTERNAL_API_KEY_WORKSPACE_TO_CONSOLE", _WORKSPACE_KEY)

    pool = object()
    find_due = AsyncMock(
        return_value={
            "status": "ready",
            "workspaces": 1,
            "due": [],
            "failures": [],
        }
    )
    monkeypatch.setattr(operations.auth, "pool", AsyncMock(return_value=pool))
    monkeypatch.setattr(operations.scheduled_runtime, "find_due_agents", find_due)
    process_pending = AsyncMock(return_value={"status": "ready", "dispatched": 0})
    monkeypatch.setattr(
        operations.grounded_analysis,
        "process_pending_analyses",
        process_pending,
    )

    return (
        TestClient(main.app, raise_server_exceptions=False),
        pool,
        find_due,
        process_pending,
        main,
    )


def test_airflow_pair_key_reaches_agent_runner_router(scheduler_http):
    client, pool, find_due, process_pending, _ = scheduler_http

    response = client.post(
        _PATH,
        headers={
            "X-Api-Key": _AIRFLOW_KEY,
            "X-Internal-Service": "airflow",
        },
        json=_WINDOW,
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "ready"
    find_due.assert_awaited_once()
    assert find_due.await_args.args == (pool,)
    process_pending.assert_awaited_once_with()


@pytest.mark.parametrize(
    "headers",
    [
        {"X-Internal-Service": "airflow"},
        {"X-Api-Key": "wrong-key", "X-Internal-Service": "airflow"},
        {
            "X-Api-Key": _WORKSPACE_KEY,
            "X-Internal-Service": "workspace",
        },
    ],
    ids=("missing-key", "wrong-key", "other-service"),
)
def test_agent_runner_rejects_every_non_airflow_authority(
    scheduler_http,
    headers: dict[str, str],
):
    client, _, find_due, process_pending, _ = scheduler_http

    response = client.post(_PATH, headers=headers, json=_WINDOW)

    assert response.status_code == 403, response.text
    find_due.assert_not_awaited()
    process_pending.assert_not_awaited()


def test_agent_runner_route_is_not_public_and_operations_health_stays_private(
    scheduler_http,
):
    client, _, _, _, main = scheduler_http

    assert not main._is_auth_public_path(_PATH)
    response = client.get("/api/operations/health")

    assert response.status_code == 401, response.text
    assert response.json() == {"detail": "authentication required"}
