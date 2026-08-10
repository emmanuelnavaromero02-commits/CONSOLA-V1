from __future__ import annotations

import asyncio
import inspect
from unittest.mock import AsyncMock


def _load_router():
    from app.routers import copilot_workflows

    return copilot_workflows


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_execute_endpoint_authenticated_only():
    mod = _load_router()
    sig = inspect.signature(mod.execute_workflow_plural)
    user_default = sig.parameters["user"].default
    assert getattr(user_default, "dependency", None).__name__ in {
        "require_authenticated",
        "get_current_user",
    }
    route = next(
        r for r in mod.plural_router.routes if r.path.endswith("/{workflow_id}/execute")
    )
    assert {"POST"} == route.methods
    dep_names = {getattr(dep.dependency, "__name__", "") for dep in route.dependencies}
    assert "require_csrf" in dep_names
    assert any(
        getattr(dep.dependency, "required_permission", None) == "copilot.write"
        for dep in route.dependencies
    )


def test_approve_endpoint_requires_execute_permission():
    mod = _load_router()
    route = next(
        r
        for r in mod.plural_router.routes
        if r.path.endswith("/{workflow_id}/steps/{step_idx}/approve")
    )
    dep_names = {getattr(dep.dependency, "__name__", "") for dep in route.dependencies}
    assert "require_csrf" in dep_names
    assert any(
        getattr(dep.dependency, "required_permission", None) == "copilot.execute"
        for dep in route.dependencies
    )


def test_execute_endpoint_returns_workflow_id(monkeypatch):
    mod = _load_router()
    monkeypatch.setattr(
        mod.workflow_executor,
        "execute_workflow",
        AsyncMock(
            return_value={"ok": True, "workflow_id": "123", "status": "completed"}
        ),
    )

    out = run(
        mod.execute_workflow_plural(
            "00000000-0000-0000-0000-000000000123",
            {"id": 1, "email": "u@example.com"},
        )
    )

    assert out["workflow_id"] == "123"
    assert out["status"] == "completed"


def test_status_endpoint_returns_step_results(monkeypatch):
    mod = _load_router()
    monkeypatch.setattr(
        mod.workflow_executor,
        "workflow_status",
        AsyncMock(
            return_value={
                "ok": True,
                "workflow_id": "123",
                "status": "running",
                "step_results": [{"step_idx": 0, "status": "completed"}],
            }
        ),
    )

    out = run(
        mod.workflow_status_plural(
            "00000000-0000-0000-0000-000000000123",
            {"id": 1, "email": "u@example.com"},
        )
    )

    assert out["status"] == "running"
    assert out["step_results"][0]["status"] == "completed"


def test_approve_step_endpoint_resumes_workflow(monkeypatch):
    mod = _load_router()
    monkeypatch.setattr(
        mod.workflow_executor,
        "approve_step",
        AsyncMock(
            return_value={
                "ok": True,
                "workflow_id": "123",
                "status": "completed",
                "step_results": [{"step_idx": 0, "status": "completed"}],
            }
        ),
    )

    out = run(
        mod.approve_workflow_step_plural(
            "00000000-0000-0000-0000-000000000123",
            0,
            {"id": 1, "email": "u@example.com"},
        )
    )

    assert out["status"] == "completed"
    mod.workflow_executor.approve_step.assert_awaited_once()
