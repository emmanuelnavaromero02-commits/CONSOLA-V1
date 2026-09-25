from __future__ import annotations

import importlib
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

_PATH = "/api/operations/internal/control-room/narrate-alerts"
_AIRFLOW_KEY = "airflow-to-console-test-key-with-sufficient-entropy"
_WORKSPACE_KEY = "workspace-to-console-test-key-with-sufficient-entropy"
_COUNTS = {
    "status": "ready",
    "workspaces": 1,
    "candidates": 0,
    "narrated_ready": 0,
    "narrated_template": 0,
    "skipped_current": 0,
    "budget_exhausted": 0,
    "lost_claim": 0,
    "deferred": 0,
    "failures": [],
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
def narrate_http(monkeypatch: pytest.MonkeyPatch):
    main, operations = _load_console_modules()

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("INTERNAL_API_KEY_AIRFLOW_TO_CONSOLE", _AIRFLOW_KEY)
    monkeypatch.setenv("INTERNAL_API_KEY_WORKSPACE_TO_CONSOLE", _WORKSPACE_KEY)

    pool = object()
    narrate = AsyncMock(return_value=dict(_COUNTS))
    monkeypatch.setattr(operations.auth, "pool", AsyncMock(return_value=pool))
    monkeypatch.setattr(
        operations.narrative_job, "narrate_pending_alerts", narrate
    )

    return TestClient(main.app, raise_server_exceptions=False), pool, narrate, main


def test_airflow_pair_key_reaches_narrate_alerts_router(narrate_http):
    client, pool, narrate, _ = narrate_http

    response = client.post(
        _PATH,
        headers={
            "X-Api-Key": _AIRFLOW_KEY,
            "X-Internal-Service": "airflow",
        },
    )

    assert response.status_code == 200, response.text
    assert response.json() == _COUNTS
    narrate.assert_awaited_once()
    assert narrate.await_args.args == (pool,)


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
def test_narrate_alerts_rejects_every_non_airflow_authority(
    narrate_http,
    headers: dict[str, str],
):
    client, _, narrate, _ = narrate_http

    response = client.post(_PATH, headers=headers)

    assert response.status_code == 403, response.text
    narrate.assert_not_awaited()


def test_narrate_alerts_rejects_unauthenticated_request(narrate_http):
    client, _, narrate, _ = narrate_http

    response = client.post(_PATH)

    assert response.status_code in {401, 403}, response.text
    narrate.assert_not_awaited()


def test_narrate_alerts_route_is_internal_dependency_not_public(narrate_http):
    client, _, _, main = narrate_http

    assert not main._is_auth_public_path(_PATH)
    assert ("POST", _PATH) in main._AUTH_INTERNAL_DEPENDENCY_REQUESTS
    assert ("GET", _PATH) not in main._AUTH_INTERNAL_DEPENDENCY_REQUESTS
    response = client.get("/api/operations/health")

    assert response.status_code == 401, response.text
    assert response.json() == {"detail": "authentication required"}
