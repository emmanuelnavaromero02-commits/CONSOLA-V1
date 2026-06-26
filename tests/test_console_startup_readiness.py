from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest
from starlette.requests import Request

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("INTERNAL_API_KEY", "test-internal-api-key-for-startup-readiness")

import app.main as main


def _request(*, authenticated: bool = False, query_string: bytes = b"") -> Request:
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/readyz",
            "headers": [],
            "query_string": query_string,
            "app": main.app,
        }
    )
    if authenticated:
        request.state.user = {"id": "operator"}
    return request


def test_startup_failure_recorder_marks_critical_errors_not_ready():
    fake_app = SimpleNamespace(state=SimpleNamespace())
    main._reset_startup_readiness_state(fake_app)

    main._record_startup_failure(
        fake_app,
        "seed_packaged_apps",
        RuntimeError("hubspot_token=pat-na1-secret-value"),
        critical=True,
    )

    assert fake_app.state.startup_ok is False
    assert fake_app.state.startup_errors[0]["component"] == "seed_packaged_apps"
    assert fake_app.state.startup_errors[0]["critical"] is True
    assert "secret-value" not in fake_app.state.startup_errors[0]["error"]
    assert "***REDACTED***" in fake_app.state.startup_errors[0]["error"]


@pytest.mark.asyncio
async def test_readyz_returns_503_without_public_startup_error_details():
    original_ok = getattr(main.app.state, "startup_ok", True)
    original_errors = list(getattr(main.app.state, "startup_errors", []) or [])
    try:
        main.app.state.startup_ok = False
        main.app.state.startup_errors = [
            {
                "component": "seed_packaged_apps",
                "critical": True,
                "error": "RuntimeError: vault_value=super-secret",
            }
        ]

        response = await main.readyz(_request())

        assert response.status_code == 503
        assert json.loads(response.body.decode()) == {
            "ok": False,
            "service": "console",
        }
    finally:
        main.app.state.startup_ok = original_ok
        main.app.state.startup_errors = original_errors


@pytest.mark.asyncio
async def test_readyz_without_require_data_skips_expensive_data_checks(monkeypatch):
    original_ok = getattr(main.app.state, "startup_ok", True)
    original_errors = list(getattr(main.app.state, "startup_errors", []) or [])

    class _Conn:
        async def fetchval(self, *_args, **_kwargs):
            return 1

    class _Acquire:
        async def __aenter__(self):
            return _Conn()

        async def __aexit__(self, *_args):
            return False

    class _Pool:
        def acquire(self):
            return _Acquire()

    async def _pool():
        return _Pool()

    async def _up(*_args, **_kwargs):
        return {"status": "up"}

    async def _expensive(*_args, **_kwargs):
        raise AssertionError("data readiness should not run for plain /readyz")

    try:
        main.app.state.startup_ok = True
        main.app.state.startup_errors = []
        monkeypatch.setattr(main, "_get_db_pool", _pool)
        monkeypatch.setattr(main, "_dependency_health", _up)
        monkeypatch.setattr(main, "_control_room_data_check", _expensive)

        response = await main.readyz(_request(authenticated=True))

        body = json.loads(response.body.decode())
        assert response.status_code == 200
        assert body["checks"]["control_room_data"] == {
            "status": "up",
            "required": False,
            "reason": "data_check_not_required",
        }
        assert body["checks"]["intelligence_data"] == {
            "status": "up",
            "required": False,
            "reason": "data_check_not_required",
        }
    finally:
        main.app.state.startup_ok = original_ok
        main.app.state.startup_errors = original_errors


@pytest.mark.asyncio
async def test_readyz_authenticated_operator_sees_only_failed_components():
    original_ok = getattr(main.app.state, "startup_ok", True)
    original_errors = list(getattr(main.app.state, "startup_errors", []) or [])
    try:
        main.app.state.startup_ok = False
        main.app.state.startup_errors = [
            {
                "component": "seed_packaged_apps",
                "critical": True,
                "error": "RuntimeError: vault_value=super-secret",
            }
        ]

        response = await main.readyz(_request(authenticated=True))

        body = json.loads(response.body.decode())
        assert response.status_code == 503
        assert body["checks"]["startup"] == {
            "status": "down",
            "critical_failures": 1,
            "components": ["seed_packaged_apps"],
        }
        assert "super-secret" not in json.dumps(body)
        assert "vault_value" not in json.dumps(body)
    finally:
        main.app.state.startup_ok = original_ok
        main.app.state.startup_errors = original_errors


class _ReadyzSuccessClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def get(self, url):
        return SimpleNamespace(status_code=200)


class _ReadyzTimeoutClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def get(self, url):
        raise main.httpx.ReadTimeout("slow dependency")


@pytest.mark.asyncio
async def test_dependency_health_uses_recent_up_signal_for_transient_timeout(monkeypatch):
    main._READYZ_DEPENDENCY_CACHE.clear()
    now = {"value": 100.0}
    monkeypatch.setattr(main.time, "monotonic", lambda: now["value"])
    monkeypatch.setenv("READYZ_DEPENDENCY_CACHE_TTL_SECONDS", "0.5")
    monkeypatch.setenv("READYZ_DEPENDENCY_STALE_TTL_SECONDS", "60")

    monkeypatch.setattr(main.httpx, "AsyncClient", _ReadyzSuccessClient)
    assert await main._dependency_health("refinement", "http://refinement/healthz") == {
        "status": "up",
        "code": 200,
    }

    now["value"] = 102.0
    monkeypatch.setattr(main.httpx, "AsyncClient", _ReadyzTimeoutClient)

    result = await main._dependency_health("refinement", "http://refinement/healthz")

    assert result == {
        "status": "up",
        "code": 200,
        "cached": True,
        "stale": True,
        "last_error": "ReadTimeout",
    }


@pytest.mark.asyncio
async def test_dependency_health_does_not_mask_expired_timeout(monkeypatch):
    main._READYZ_DEPENDENCY_CACHE.clear()
    now = {"value": 100.0}
    monkeypatch.setattr(main.time, "monotonic", lambda: now["value"])
    monkeypatch.setenv("READYZ_DEPENDENCY_CACHE_TTL_SECONDS", "0.5")
    monkeypatch.setenv("READYZ_DEPENDENCY_STALE_TTL_SECONDS", "10")

    monkeypatch.setattr(main.httpx, "AsyncClient", _ReadyzSuccessClient)
    await main._dependency_health("refinement", "http://refinement/healthz")

    now["value"] = 120.0
    monkeypatch.setattr(main.httpx, "AsyncClient", _ReadyzTimeoutClient)

    assert await main._dependency_health("refinement", "http://refinement/healthz") == {
        "status": "down",
        "error": "ReadTimeout",
    }
