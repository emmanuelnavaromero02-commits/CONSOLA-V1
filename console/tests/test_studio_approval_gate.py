from __future__ import annotations

import os

import pytest
from fastapi import HTTPException

from studio_goal_fakes import FakeStudioGoalPool, pool_factory


@pytest.mark.asyncio
async def test_execute_goal_run_pauses_before_write_step(monkeypatch):
    os.environ["APP_ENV"] = "test"
    from app.services import studio_goal_runs

    pool = FakeStudioGoalPool()
    audits = []
    executed = []

    async def audit(**kwargs):
        audits.append(kwargs)

    async def executor(_run, step, _user):
        executed.append(step["step_key"])
        return {"ok": True, "step_key": step["step_key"]}

    monkeypatch.setattr(studio_goal_runs.auth, "pool", pool_factory(pool), raising=False)
    monkeypatch.setattr(studio_goal_runs.audit_service, "record_event", audit)

    created = await studio_goal_runs.create_goal_run("hubspot", "validar", {"id": 7}, auto_plan=True)
    goal_run_id = created["goal_run"]["id"]
    result = await studio_goal_runs.execute_goal_run(goal_run_id, {"id": 7}, executor=executor)

    assert result["approval_required"] is True
    assert result["approval"]["tool"] == "cartridge_extract_all"
    assert result["approval"]["risk_level"] == "write"
    assert "extraction_smoke" not in executed
    assert executed == [
        "load_manifest",
        "connector_schema",
        "vault_credentials",
        "introspect_source",
        "compare_entities",
        "validate_dags",
    ]
    assert any(item["status"] == "pending_approval" for item in audits)


@pytest.mark.asyncio
async def test_admin_execute_goal_run_does_not_pause_on_internal_write_step(monkeypatch):
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

    user = {"id": 7, "role": "admin", "workspace_role": "admin"}
    created = await studio_goal_runs.create_goal_run("hubspot", "validar", user, auto_plan=True)
    goal_run_id = created["goal_run"]["id"]
    result = await studio_goal_runs.execute_goal_run(goal_run_id, user, executor=executor)

    assert result["goal_run"]["status"] == "completed"
    assert "extraction_smoke" in executed
    assert all(step["status"] == "completed" for step in result["steps"])


@pytest.mark.asyncio
async def test_approval_requires_matching_approval_key(monkeypatch):
    os.environ["APP_ENV"] = "test"
    from app.services import studio_goal_runs

    pool = FakeStudioGoalPool()

    async def audit(**_kwargs):
        return None

    async def executor(_run, step, _user):
        return {"ok": True, "step_key": step["step_key"]}

    monkeypatch.setattr(studio_goal_runs.auth, "pool", pool_factory(pool), raising=False)
    monkeypatch.setattr(studio_goal_runs.audit_service, "record_event", audit)

    created = await studio_goal_runs.create_goal_run("hubspot", "validar", {"id": 7}, auto_plan=True)
    goal_run_id = created["goal_run"]["id"]
    paused = await studio_goal_runs.execute_goal_run(goal_run_id, {"id": 7}, executor=executor)
    step_id = paused["approval"]["step_id"]

    with pytest.raises(HTTPException) as missing:
        await studio_goal_runs.approve_goal_step(goal_run_id, step_id, {"id": 7})
    assert missing.value.status_code == 400

    with pytest.raises(HTTPException) as wrong:
        await studio_goal_runs.approve_goal_step(
            goal_run_id,
            step_id,
            {"id": 7},
            approval_key="00000000-0000-0000-0000-000000000000",
        )
    assert wrong.value.status_code == 409
