from __future__ import annotations

import os

import pytest

from studio_goal_fakes import FakeStudioGoalPool, pool_factory


@pytest.mark.asyncio
async def test_create_goal_run_persists_and_plans(monkeypatch):
    os.environ["APP_ENV"] = "test"
    from app.services import studio_goal_runs

    pool = FakeStudioGoalPool()
    audits = []

    async def audit(**kwargs):
        audits.append(kwargs)

    monkeypatch.setattr(studio_goal_runs.auth, "pool", pool_factory(pool), raising=False)
    monkeypatch.setattr(studio_goal_runs.audit_service, "record_event", audit)

    result = await studio_goal_runs.create_goal_run(
        "hubspot",
        "valida HubSpot para producción",
        {"id": 7, "email": "admin@local.ai"},
    )

    run = result["goal_run"]
    steps = result["steps"]
    assert run["cartridge_id"] == "hubspot"
    assert run["status"] == "running"
    assert len(steps) == 10
    assert steps[0]["step_key"] == "load_manifest"
    assert any(step["step_key"] == "extraction_smoke" and step["risk_level"] == "write" for step in steps)
    assert any(item["action"] == "studio.goal.create" for item in audits)
    assert any(item["action"] == "studio.goal.plan" for item in audits)


def test_default_goal_step_tools_are_real_studio_tools():
    from app.services import studio_assistant, studio_goal_runs

    known = set(studio_assistant.STUDIO_TOOLS_WHITELIST)
    missing = {step["tool"] for step in studio_goal_runs.default_steps("hubspot") if step["tool"] not in known}

    assert missing == set()
