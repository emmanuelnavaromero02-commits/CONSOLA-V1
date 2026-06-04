from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest
from starlette.requests import Request

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("INTERNAL_API_KEY", "test-internal-api-key-for-startup-readiness")

import app.main as main


def _request(*, authenticated: bool = False) -> Request:
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/readyz",
            "headers": [],
            "query_string": b"",
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
