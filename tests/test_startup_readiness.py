from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import startup_readiness


def _fake_app():
    return SimpleNamespace(state=SimpleNamespace())


def test_record_startup_failure_redacts_secret_and_marks_critical_down():
    app = _fake_app()
    startup_readiness.reset_startup_readiness_state(app)

    startup_readiness.record_startup_failure(
        app,
        "seed_packaged_apps",
        RuntimeError("hubspot_token=pat-na1-secret-value"),
    )

    assert app.state.startup_ok is False
    assert app.state.startup_errors[0]["component"] == "seed_packaged_apps"
    assert app.state.startup_errors[0]["critical"] is True
    assert "secret-value" not in app.state.startup_errors[0]["error"]
    assert "***REDACTED***" in app.state.startup_errors[0]["error"]


def test_record_noncritical_startup_failure_keeps_readiness_up():
    app = _fake_app()
    startup_readiness.reset_startup_readiness_state(app)

    startup_readiness.record_startup_failure(
        app,
        "optional_seed",
        RuntimeError("temporary"),
        critical=False,
    )

    assert app.state.startup_ok is True
    assert startup_readiness.startup_readiness_status(app) == {
        "status": "up",
        "critical_failures": 0,
    }


@pytest.mark.asyncio
async def test_run_startup_seed_records_runner_failures():
    app = _fake_app()
    startup_readiness.reset_startup_readiness_state(app)

    async def fail():
        raise RuntimeError("bad seed")

    await startup_readiness.run_startup_seed(app, "seed", fail)

    assert startup_readiness.startup_readiness_status(app) == {
        "status": "down",
        "critical_failures": 1,
        "components": ["seed"],
    }
