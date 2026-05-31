from __future__ import annotations

import os

import pytest

from studio_goal_fakes import FakeStudioGoalPool, pool_factory


@pytest.mark.asyncio
async def test_approved_goal_run_continues_and_completes(monkeypatch):
    os.environ["APP_ENV"] = "test"
    from app.services import studio_goal_runs

    pool = FakeStudioGoalPool()
    executed = []

    async def audit(**_kwargs):
        return None

    async def executor(_run, step, _user):
        executed.append(step["step_key"])
        return {"ok": True, "step_key": step["step_key"]}

    monkeypatch.setattr(studio_goal_runs.auth, "pool", pool_factory(pool), raising=False)
    monkeypatch.setattr(studio_goal_runs.audit_service, "record_event", audit)

    created = await studio_goal_runs.create_goal_run("hubspot", "validar", {"id": 7}, auto_plan=True)
    goal_run_id = created["goal_run"]["id"]
    paused = await studio_goal_runs.execute_goal_run(goal_run_id, {"id": 7}, executor=executor)
    step_id = paused["approval"]["step_id"]
    approval_key = paused["approval"]["approval_key"]

    approval = await studio_goal_runs.approve_goal_step(goal_run_id, step_id, {"id": 7}, approval_key=approval_key)
    assert approval["step"]["status"] == "pending"

    completed = await studio_goal_runs.execute_goal_run(goal_run_id, {"id": 7}, executor=executor)

    assert "extraction_smoke" in executed
    assert completed["goal_run"]["status"] == "completed"
    assert all(step["status"] == "completed" for step in completed["steps"])
