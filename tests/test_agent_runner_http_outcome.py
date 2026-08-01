from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tests.airflow_dag_test_loader import load_dag, load_module


class _Response:
    def __init__(
        self, status_code: int, payload: object, *, invalid_json: bool = False
    ):
        self.status_code = status_code
        self.payload = payload
        self.invalid_json = invalid_json

    def json(self):
        if self.invalid_json:
            raise ValueError("not json")
        return self.payload


class _TaskInstance:
    def __init__(self, due: list[dict]):
        self.due = due

    def xcom_pull(self, *, task_ids: str, key: str | None = None):
        if task_ids == "find_due_agents" and key == "due":
            return self.due
        if task_ids == "find_due_agents" and key == "discovery":
            return {"status": "ready", "workspaces": 1, "scope_failures": 0}
        return None


def _context() -> dict:
    return {
        "run_id": "scheduled__agent_runner",
        "ti": _TaskInstance(
            [
                {
                    "id": "agent-a",
                    "prompt": "Review operations",
                    "tenant_id": "tenant-a",
                    "workspace_id": "workspace-a",
                    "scheduled_fire_at": "2026-08-01T00:00:00+00:00",
                }
            ]
        ),
    }


def _valid_payload() -> dict:
    return {
        "ok": True,
        "status": "completed",
        "reply": "Monitor completed",
        "viewer_urls": [],
        "messages": [],
        "agent_id": "agent-a",
        "run_id": 17,
        "deterministic_monitor": True,
        "monitor": {
            "status": "ready",
            "signals": 1,
            "blockers": 0,
            "engines": [],
            "alerted": False,
        },
        "schedule_run": {"id": 9, "status": "ok", "fencing_token": 1},
    }


def _modules(monkeypatch: pytest.MonkeyPatch):
    record = load_module(
        monkeypatch, "agent_runner_record", alias="agent_runner_record"
    )
    return load_dag(monkeypatch, "agent_runner"), record


def _invoke(monkeypatch: pytest.MonkeyPatch, agent_runner, response: _Response) -> dict:
    monkeypatch.setattr(agent_runner, "RUNNER_TOKEN", "runner-token")
    monkeypatch.setattr(agent_runner, "_internal_key", lambda _name: "internal-key")
    monkeypatch.setattr(agent_runner.requests, "post", lambda *_a, **_k: response)
    return agent_runner.invoke_each(**_context())


@pytest.mark.parametrize(
    "response",
    [
        _Response(200, None, invalid_json=True),
        _Response(200, {}),
        _Response(200, {"ok": False}),
        _Response(200, {"error": "private"}),
        _Response(200, {"ok": True, "status": "running"}),
        _Response(200, {"ok": True, "status": "completed", "run_id": "bad"}),
        _Response(500, _valid_payload()),
    ],
)
def test_agent_runner_rejects_invalid_scheduled_outcomes(monkeypatch, response) -> None:
    agent_runner, agent_runner_record = _modules(monkeypatch)
    result = _invoke(monkeypatch, agent_runner, response)

    assert result["invoked"] == 0
    assert result["results"][0]["ok"] is False
    assert agent_runner_record._status(result) == "failed"


def test_agent_runner_accepts_only_complete_typed_outcome(monkeypatch) -> None:
    agent_runner, agent_runner_record = _modules(monkeypatch)
    result = _invoke(monkeypatch, agent_runner, _Response(200, _valid_payload()))

    assert result["invoked"] == 1
    assert result["results"] == [
        {"ok": True, "status": 200, "duplicate": False, "run_id": 17}
    ]
    assert agent_runner_record._status(result) == "success"


def test_running_duplicate_is_not_a_terminal_success(monkeypatch) -> None:
    agent_runner, agent_runner_record = _modules(monkeypatch)
    payload = {
        "ok": False,
        "status": "running",
        "duplicate": True,
        "run_id": 17,
        "schedule_run": {"id": 9, "status": "running", "fencing_token": 1},
    }

    result = _invoke(monkeypatch, agent_runner, _Response(200, payload))

    assert result["invoked"] == 0
    assert agent_runner_record._status(result) == "failed"


def test_record_uses_validated_result_for_successful_count(monkeypatch) -> None:
    _agent_runner, agent_runner_record = _modules(monkeypatch)
    invocation = {
        "operational_status": "ready",
        "workspace_count": 1,
        "scope_failures": 0,
        "results": [
            {"ok": True, "status": 200, "duplicate": False, "run_id": 17},
            {"ok": False, "status": 200, "error_code": "invalid_outcome"},
        ],
    }

    assert agent_runner_record._status(invocation) == "partial"


def test_registry_rejects_http_200_ok_false(monkeypatch) -> None:
    _agent_runner, agent_runner_record = _modules(monkeypatch)

    class _RegistryTI:
        def xcom_pull(self, *, task_ids: str):
            return {
                "operational_status": "ready",
                "workspace_count": 1,
                "scope_failures": 0,
                "results": [
                    {"ok": True, "status": 200, "duplicate": False, "run_id": 17}
                ],
            }

    monkeypatch.setattr(
        agent_runner_record.requests,
        "post",
        lambda *_a, **_k: _Response(200, {"ok": False}),
    )
    with pytest.raises(RuntimeError, match="could not be recorded"):
        agent_runner_record.record_agent_runner_run(
            {
                "ti": _RegistryTI(),
                "run_id": "scheduled__agent_runner",
                "logical_date": datetime(2026, 8, 1, tzinfo=timezone.utc),
            },
            mcp_url="http://mcp-infra",
            headers=lambda: {},
            cartridge_id="platform",
            entity="AgentRunner",
            dag_id="agent_runner",
        )
