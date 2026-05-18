from __future__ import annotations

import asyncio
import copy
import json
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock

import pytest


REPO = Path(__file__).resolve().parents[1]


@pytest.fixture()
def executor_module(monkeypatch):
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    siblings = ("/cartridges/", "/console", "/refinement", "/vault", "/workspace", "/mcp-infra")
    sys.path[:] = [p for p in sys.path if not any(marker in p for marker in siblings)]
    sys.path.insert(0, str(REPO / "console"))
    from app.services import workflow_executor as mod

    monkeypatch.setattr(mod, "BASE_BACKOFF_SECONDS", 0)
    return mod


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class FakePool:
    def __init__(self):
        self.workflow_id = str(uuid.uuid4())
        self.user_id = 7
        self.workflow = {
            "id": self.workflow_id,
            "user_id": self.user_id,
            "intent": "run workflow",
            "plan": [],
            "status": "running",
            "current_step": 0,
            "error": None,
            "created_at": None,
            "finished_at": None,
            "started_at": None,
            "completed_at": None,
            "step_results": [],
        }
        self.steps: list[dict] = []

    def add_step(self, idx: int, tool: str | None, args: dict | None = None, status: str = "pending"):
        self.steps.append({
            "id": idx + 1,
            "workflow_id": self.workflow_id,
            "step_idx": idx,
            "description": f"step {idx}",
            "tool": tool,
            "args": args or {},
            "result": {},
            "status": status,
            "started_at": None,
            "finished_at": None,
        })

    async def fetchrow(self, query: str, *args):
        q = " ".join(query.split())
        if q.startswith("SELECT id, user_id, intent, plan"):
            if args[0] == self.workflow_id and args[1] == self.user_id:
                return copy.deepcopy(self.workflow)
            return None
        if q.startswith("UPDATE workflow_steps SET status = 'running'"):
            step = self._step(args[0], args[1])
            if any(
                prev["workflow_id"] == args[0]
                and prev["step_idx"] < args[1]
                and prev["status"] not in {"completed", "skipped"}
                for prev in self.steps
            ):
                return None
            if step["status"] == "pending":
                step["status"] = "running"
                return copy.deepcopy(step)
            return None
        if q.startswith("UPDATE workflow_runs SET status = 'running', started_at"):
            if self.workflow["status"] != "cancelled":
                self.workflow["status"] = "running"
                self.workflow["started_at"] = self.workflow["started_at"] or "now"
                return {"status": "running"}
            return None
        if q.startswith("UPDATE workflow_runs SET status = 'running', current_step"):
            if self.workflow["status"] != "cancelled":
                self.workflow["status"] = "running"
                self.workflow["current_step"] = args[1]
                return {"status": "running"}
            return None
        if q.startswith("UPDATE workflow_steps SET status = 'completed', result = jsonb_set"):
            step = self._step(args[0], args[1])
            if step["status"] == "pending" and step["result"].get("approved") is True:
                step["status"] = "completed"
                step["result"]["human_review_completed"] = True
                return {"id": step["id"]}
            return None
        if q.startswith("UPDATE workflow_steps SET status = 'completed'"):
            step = self._step(args[0], args[1])
            if step["status"] == "running" and self.workflow["status"] != "cancelled":
                step["status"] = "completed"
                step["result"] = json.loads(args[2])
                return {"id": step["id"]}
            return None
        if q.startswith("UPDATE workflow_steps SET status = 'failed'"):
            step = self._step(args[0], args[1])
            if step["status"] == "running" and self.workflow["status"] != "cancelled":
                step["status"] = "failed"
                step["result"] = json.loads(args[2])
                return {"id": step["id"]}
            return None
        if q.startswith("UPDATE workflow_steps SET status = 'pending'"):
            for step in self.steps:
                if step["workflow_id"] == args[0] and step["step_idx"] == args[1] and step["status"] == "waiting_approval":
                    step["status"] = "pending"
                    step["result"]["approved"] = True
                    return copy.deepcopy(step)
            return None
        if q.startswith("UPDATE workflow_runs SET status = 'completed'"):
            if self.workflow["status"] != "cancelled":
                self.workflow["status"] = "completed"
                return {"status": "completed"}
            return None
        if q.startswith("UPDATE workflow_runs SET status = 'failed'"):
            if self.workflow["status"] != "cancelled":
                self.workflow["status"] = "failed"
                self.workflow["error"] = args[1]
                return {"status": "failed"}
            return None
        if q.startswith("UPDATE workflow_runs SET status = 'cancelled'"):
            if args[0] == self.workflow_id and args[1] == self.user_id and self.workflow["status"] in {"planning", "running", "waiting_approval"}:
                self.workflow["status"] = "cancelled"
                return {"id": self.workflow_id, "status": "cancelled"}
            return None
        raise AssertionError(f"unmocked fetchrow: {q[:140]}")

    async def fetch(self, query: str, *args):
        q = " ".join(query.split())
        if q.startswith("SELECT id, workflow_id, step_idx"):
            return [copy.deepcopy(s) for s in sorted(self.steps, key=lambda item: item["step_idx"]) if s["workflow_id"] == args[0]]
        raise AssertionError(f"unmocked fetch: {q[:140]}")

    async def fetchval(self, query: str, *args):
        q = " ".join(query.split())
        if q.startswith("SELECT COUNT(*) FROM workflow_steps"):
            return sum(1 for s in self.steps if s["workflow_id"] == args[0])
        if q.startswith("SELECT status FROM workflow_runs"):
            return self.workflow["status"] if args[0] == self.workflow_id else None
        raise AssertionError(f"unmocked fetchval: {q[:140]}")

    async def execute(self, query: str, *args):
        q = " ".join(query.split())
        if q.startswith("UPDATE workflow_runs SET step_results"):
            self.workflow["step_results"] = json.loads(args[1])
            return "UPDATE 1"
        if q.startswith("UPDATE workflow_runs SET status = 'waiting_approval'"):
            self.workflow["status"] = "waiting_approval"
            self.workflow["current_step"] = args[1]
            return "UPDATE 1"
        if q.startswith("UPDATE workflow_steps SET status = 'waiting_approval'"):
            step = self._step(args[0], args[1])
            step["status"] = "waiting_approval"
            step["result"] = json.loads(args[2])
            return "UPDATE 1"
        if q.startswith("UPDATE workflow_steps SET status = 'skipped'"):
            for step in self.steps:
                if step["workflow_id"] == args[0] and step["step_idx"] > args[1] and step["status"] in {"pending", "waiting_approval"}:
                    step["status"] = "skipped"
            return "UPDATE 1"
        if q.startswith("INSERT INTO workflow_steps"):
            return "INSERT 0 1"
        raise AssertionError(f"unmocked execute: {q[:140]}")

    def _step(self, workflow_id: str, idx: int) -> dict:
        for step in self.steps:
            if step["workflow_id"] == workflow_id and step["step_idx"] == idx:
                return step
        raise AssertionError(f"missing step {idx}")


@pytest.fixture()
def fake_pool(executor_module, monkeypatch):
    pool = FakePool()
    monkeypatch.setattr(executor_module.auth, "pool", AsyncMock(return_value=pool))
    return pool


@pytest.fixture()
def user(fake_pool):
    return {"id": fake_pool.user_id, "email": "user@example.com", "role": "admin"}


def test_executor_runs_read_only_workflow_end_to_end(executor_module, fake_pool, user, monkeypatch):
    fake_pool.add_step(0, "infra.airflow_list_dags")

    async def invoke(server, tool, args):
        return {"dags": ["daily"]}

    monkeypatch.setattr(executor_module.mcp_registry, "invoke", invoke)
    out = run(executor_module.execute_workflow(fake_pool.workflow_id, user))

    assert out["status"] == "completed"
    assert fake_pool.steps[0]["status"] == "completed"
    assert fake_pool.steps[0]["result"] == {"dags": ["daily"]}


def test_executor_pauses_on_write_step_waiting_approval(executor_module, fake_pool, user, monkeypatch):
    fake_pool.add_step(0, "infra.unknown_write_tool", {"x": 1})
    invoked = False

    async def invoke(*_):
        nonlocal invoked
        invoked = True
        return {"ok": True}

    monkeypatch.setattr(executor_module.mcp_registry, "invoke", invoke)
    out = run(executor_module.execute_workflow(fake_pool.workflow_id, user))

    assert out["status"] == "waiting_approval"
    assert fake_pool.steps[0]["status"] == "waiting_approval"
    assert fake_pool.steps[0]["result"]["approval_required"] is True
    assert invoked is False


def test_executor_retries_transient_failures(executor_module, fake_pool, user, monkeypatch):
    fake_pool.add_step(0, "infra.airflow_list_dags")
    calls = 0

    async def invoke(*_):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise RuntimeError("temporary")
        return {"ok": True}

    monkeypatch.setattr(executor_module.mcp_registry, "invoke", invoke)
    out = run(executor_module.execute_workflow(fake_pool.workflow_id, user))

    assert calls == 3
    assert out["status"] == "completed"


def test_executor_fails_fast_after_3_retries(executor_module, fake_pool, user, monkeypatch):
    fake_pool.add_step(0, "infra.airflow_list_dags")
    fake_pool.add_step(1, "infra.airflow_get_run_status")
    calls = 0

    async def invoke(*_):
        nonlocal calls
        calls += 1
        raise RuntimeError("down")

    monkeypatch.setattr(executor_module.mcp_registry, "invoke", invoke)
    out = run(executor_module.execute_workflow(fake_pool.workflow_id, user))

    assert calls == 3
    assert out["status"] == "failed"
    assert fake_pool.steps[0]["status"] == "failed"
    assert fake_pool.steps[1]["status"] == "skipped"


def test_executor_does_not_retry_semantic_tool_errors(executor_module, fake_pool, user, monkeypatch):
    fake_pool.add_step(0, "infra.airflow_list_dags")
    calls = 0

    async def invoke(*_):
        nonlocal calls
        calls += 1
        return {"error": "permission denied"}

    monkeypatch.setattr(executor_module.mcp_registry, "invoke", invoke)
    out = run(executor_module.execute_workflow(fake_pool.workflow_id, user))

    assert calls == 1
    assert out["status"] == "failed"


def test_executor_does_not_run_later_step_while_previous_is_running(executor_module, fake_pool, user, monkeypatch):
    fake_pool.add_step(0, "infra.airflow_list_dags", status="running")
    fake_pool.add_step(1, "infra.airflow_get_run_status")
    invoked = False

    async def invoke(*_):
        nonlocal invoked
        invoked = True
        return {"ok": True}

    monkeypatch.setattr(executor_module.mcp_registry, "invoke", invoke)
    out = run(executor_module.execute_workflow(fake_pool.workflow_id, user))

    assert out["status"] == "running"
    assert fake_pool.steps[1]["status"] == "pending"
    assert invoked is False


def test_executor_does_not_trust_plan_args_approved(executor_module, fake_pool, user, monkeypatch):
    fake_pool.add_step(0, "infra.unknown_write_tool", {"approved": True})
    invoked = False

    async def invoke(*_):
        nonlocal invoked
        invoked = True
        return {"ok": True}

    monkeypatch.setattr(executor_module.mcp_registry, "invoke", invoke)
    out = run(executor_module.execute_workflow(fake_pool.workflow_id, user))

    assert out["status"] == "waiting_approval"
    assert invoked is False


def test_executor_human_step_resumes_after_approval(executor_module, fake_pool, user):
    fake_pool.add_step(0, None)

    out = run(executor_module.execute_workflow(fake_pool.workflow_id, user))
    assert out["status"] == "waiting_approval"

    out = run(executor_module.approve_step(fake_pool.workflow_id, 0, user))
    assert out["status"] == "completed"
    assert fake_pool.steps[0]["status"] == "completed"


def test_executor_approval_requires_execute_permission(executor_module, fake_pool):
    fake_pool.add_step(0, None, status="waiting_approval")
    user = {"id": fake_pool.user_id, "email": "viewer@example.com", "role": "viewer"}

    with pytest.raises(Exception) as exc:
        run(executor_module.approve_step(fake_pool.workflow_id, 0, user))

    assert getattr(exc.value, "status_code", None) == 403


def test_executor_cancels_running_workflow_cleanly(executor_module, fake_pool, user):
    fake_pool.add_step(0, "infra.airflow_list_dags")
    out = run(executor_module.cancel_workflow(fake_pool.workflow_id, user))

    assert out["status"] == "cancelled"
    assert fake_pool.workflow["status"] == "cancelled"
    assert fake_pool.steps[0]["status"] == "skipped"


def test_executor_audit_trail_per_step(executor_module, fake_pool, user, monkeypatch):
    fake_pool.add_step(0, "infra.airflow_list_dags")
    audit_calls = []

    async def record_event(**kwargs):
        audit_calls.append(kwargs)

    async def invoke(*_):
        return {"ok": True}

    monkeypatch.setattr(executor_module.audit_service, "record_event", record_event)
    monkeypatch.setattr(executor_module.mcp_registry, "invoke", invoke)

    run(executor_module.execute_workflow(fake_pool.workflow_id, user))

    assert audit_calls
    assert audit_calls[0]["action"] == "copilot.workflow.step"
    assert audit_calls[0]["metadata"]["step_index"] == 0
    assert audit_calls[0]["status"] == "success"


def test_executor_skipped_steps_after_failure(executor_module, fake_pool, user, monkeypatch):
    fake_pool.add_step(0, "infra.airflow_list_dags")
    fake_pool.add_step(1, "infra.airflow_get_run_status")
    fake_pool.add_step(2, "infra.airflow_list_dags")

    async def invoke(*_):
        return {"error": "still down"}

    monkeypatch.setattr(executor_module.mcp_registry, "invoke", invoke)
    run(executor_module.execute_workflow(fake_pool.workflow_id, user))

    assert [s["status"] for s in fake_pool.steps] == ["failed", "skipped", "skipped"]
