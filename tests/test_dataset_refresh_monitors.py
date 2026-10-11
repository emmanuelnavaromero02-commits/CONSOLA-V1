from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from airflow.dags import dataset_refresh_monitors as monitors
from tests.airflow_dag_test_loader import load_dag


KEY = "refresh-monitor-run-authority-key-with-entropy-01"
TENANT = "11111111-1111-4111-8111-111111111111"
WORKSPACE = "22222222-2222-4222-8222-222222222222"
REFRESH = "dataset_refresh_chain:successfactors__dataset_refresh__abc"


@pytest.fixture(autouse=True)
def _signing_key(monkeypatch):
    monkeypatch.setenv("SECURITY_CONTEXT_SIGNING_KEY", KEY)


class _Response:
    def __init__(self, status_code: int, payload: object):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def _completed(run_id: int, *, duplicate: bool = False) -> dict:
    payload = {
        "ok": True,
        "status": "completed",
        "duplicate": duplicate,
        "run_id": run_id,
        "schedule_run": {"status": "ok", "id": run_id, "fencing_token": 1},
    }
    if not duplicate:
        payload["deterministic_monitor"] = True
        payload["monitor"] = {
            "status": "ok",
            "signals": 2,
            "blockers": 0,
            "engines": [],
            "alerted": True,
        }
    return payload


_NARRATED = _Response(200, {"status": "ready", "candidates": 2, "narrated_template": 2, "failures": []})


class _Console:
    def __init__(self, due: _Response, invocations: list[_Response], narration: _Response = _NARRATED):
        self.due = due
        self.invocations = list(invocations)
        self.narration = narration
        self.calls: list[tuple[str, dict, dict | None]] = []

    def post(self, url, *, headers, timeout, json=None):
        self.calls.append((url, headers, json))
        if url.endswith(monitors.DUE_PATH):
            return self.due
        if url.endswith(monitors.NARRATE_PATH):
            return self.narration
        return self.invocations.pop(0)


def _run(console: _Console, **overrides):
    kwargs = {
        "console_url": "http://console:8000/",
        "headers": lambda: {"X-API-Key": "k", "X-Internal-Service": "airflow"},
        "runner_token": "runner-token",
        "tenant_id": TENANT,
        "workspace_id": WORKSPACE,
        "refresh_run_id": REFRESH,
        "airflow_dag_run_id": "successfactors__dataset_refresh__abc",
        "http": console,
    }
    kwargs.update(overrides)
    return monitors.run_refresh_monitors(**kwargs)


def _due(*agents: str) -> _Response:
    return _Response(
        200,
        {"status": "ready", "due": [{"id": agent, "slug": f"slug-{agent}"} for agent in agents]},
    )


def test_every_monitor_runs_once_with_runner_authority_bound_to_the_refresh():
    console = _Console(_due("a1", "a2"), [_Response(200, _completed(7)), _Response(200, _completed(8, duplicate=True))])

    outcome = _run(console)

    assert outcome["status"] == "success"
    assert (outcome["completed"], outcome["duplicates"], outcome["failed"]) == (2, 1, 0)
    url, headers, body = console.calls[1]
    assert url == "http://console:8000/internal/automation/refresh-monitors/invoke"
    assert console.calls[-1][0].endswith(monitors.NARRATE_PATH)
    assert headers["X-Agent-Runner-Token"] == "runner-token"
    assert body == {
        "tenant_id": TENANT,
        "workspace_id": WORKSPACE,
        "refresh_run_id": REFRESH,
        "agent_id": "a1",
        "airflow_dag_run_id": "successfactors__dataset_refresh__abc",
    }


def test_alerts_are_narrated_after_the_monitors_without_the_runner_token():
    console = _Console(_due("a1"), [_Response(200, _completed(7))])

    outcome = _run(console)

    url, headers, body = console.calls[-1]
    assert url == "http://console:8000/api/operations/internal/control-room/narrate-alerts"
    assert "X-Agent-Runner-Token" not in headers and headers["X-Internal-Service"] == "airflow"
    assert body == {"tenant_id": TENANT, "workspace_id": WORKSPACE}
    assert outcome["status"] == "success"
    assert outcome["narration"]["narrated_template"] == 2
    assert _record(outcome)["args"]["extra"]["narration"]["status"] == "ready"


def test_unavailable_narration_is_reported_as_partial():
    console = _Console(_due("a1"), [_Response(200, _completed(7))], narration=_Response(503, {}))

    outcome = _run(console)

    assert outcome["status"] == "partial"
    assert outcome["reason"] == "narration_unavailable"
    assert outcome["narration"] == {"status": "unavailable", "error_code": "RuntimeError"}


def test_nothing_is_narrated_when_no_monitor_completed():
    console = _Console(_due("a1"), [_Response(500, {})])
    outcome = _run(console)
    assert outcome["status"] == "failed"
    assert "narration" not in outcome
    assert not any(url.endswith(monitors.NARRATE_PATH) for url, _h, _b in console.calls)


def test_a_failed_monitor_is_reported_without_hiding_the_others():
    console = _Console(_due("a1", "a2"), [_Response(500, {"detail": "x"}), _Response(200, _completed(9))])

    outcome = _run(console)

    assert outcome["status"] == "partial"
    assert outcome["results"][0]["status"] == "failed"
    assert outcome["results"][0]["http_status"] == 500
    assert outcome["results"][1]["status"] == "completed"


def test_invalid_monitor_responses_count_as_failures():
    bad = _completed(3)
    bad["deterministic_monitor"] = False
    console = _Console(_due("a1"), [_Response(200, bad)])
    assert _run(console)["status"] == "failed"


@pytest.mark.parametrize(
    "due",
    [
        _Response(409, {"detail": "refresh run did not publish data"}),
        _Response(200, {"status": "ready"}),
        _Response(200, {"status": "ready", "due": [{"slug": "missing-id"}]}),
    ],
)
def test_discovery_problems_are_failures_not_silent_successes(due):
    outcome = _run(_Console(due, []))
    assert outcome["status"] == "failed"
    assert outcome["reason"].startswith("discovery_unavailable")


def test_no_monitors_is_a_recorded_structural_skip():
    outcome = _run(_Console(_Response(200, {"status": "no_monitors", "due": []}), []))
    assert outcome["status"] == "skipped"
    assert outcome["reason"] == "no_monitors"


def test_missing_runner_authority_never_calls_the_console():
    console = _Console(_due("a1"), [])
    outcome = _run(console, runner_token="")
    assert outcome == {**monitors._outcome("failed", reason="runner_authority_unavailable")}
    assert console.calls == []


def test_budget_defers_remaining_monitors():
    ticks = iter([0.0, 0.0, 10_000.0])
    console = _Console(_due("a1", "a2"), [_Response(200, _completed(5))])
    outcome = _run(console, clock=lambda: next(ticks), budget_seconds=60)
    assert outcome["status"] == "partial"
    assert outcome["deferred"] == 1


def _record(outcome: dict, *, response: _Response | None = None) -> dict:
    sent: dict = {}

    class _Mcp:
        def post(self, url, *, headers, json, timeout):
            sent.update(json)
            return response or _Response(
                200, {"result": {"saved": True, "status": json["args"]["status"]}}
            )

    monitors.record_refresh_monitors(
        outcome,
        mcp_url="http://mcp-infra:8010",
        headers={},
        cartridge_id="sap_successfactors",
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        airflow_run_id="successfactors__dataset_refresh__abc",
        started_at=datetime.now(timezone.utc) - timedelta(seconds=5),
        http=_Mcp(),
    )
    return sent


def test_outcome_is_recorded_as_a_scoped_pipeline_row():
    outcome = monitors._outcome(
        "partial",
        reason="monitors_incomplete",
        total=2,
        completed=1,
        failed=1,
        results=[{"slug": "s", "status": "failed", "error_code": "RuntimeError", "reply": "x" * 900}],
    )
    sent = _record(outcome)
    args = sent["args"]

    assert args["entity"] == "AgentOpsMonitors"
    assert args["run_id"] == "dataset_refresh_chain:successfactors__dataset_refresh__abc:monitors"
    assert (args["tenant_id"], args["workspace_id"]) == (TENANT, WORKSPACE)
    assert args["status"] == "partial" and args["mode"] == "monitors"
    assert args["error_message"] == "monitors_incomplete"
    assert args["extra"]["results"] == [{"slug": "s", "status": "failed", "error_code": "RuntimeError"}]
    assert sent["security_context"]["tenant_id"] == TENANT


def test_unrecordable_outcome_raises():
    with pytest.raises(RuntimeError, match="could not be recorded"):
        _record(monitors._outcome("success"), response=_Response(500, {}))


class _Ti:
    def xcom_pull(self, *, task_ids: str, key: str | None = None):
        if task_ids == "admit_dataset_refresh":
            return {"tenant_id": TENANT, "workspace_id": WORKSPACE, "cartridge_id": "sap_successfactors"}
        if task_ids == "resolve_chain" and key == "cartridge_id":
            return "sap_successfactors"
        return None


def test_chain_runs_monitors_only_after_recording_and_never_fails_on_them(monkeypatch):
    chain = load_dag(monkeypatch, "dataset_refresh_chain")
    assert chain.t_mon.kwargs["retries"] == 0
    assert "trigger_rule" not in chain.t_mon.kwargs
    assert chain.t_mon.kwargs["execution_timeout"] > timedelta(seconds=monitors.BUDGET_SECONDS + monitors.INVOKE_TIMEOUT_SECONDS)
    source = Path(chain.__file__).read_text(encoding="utf-8")
    assert "t_admit >> t_resolve >> t_mat >> t_rec\nt_rec >> t_mon" in source

    calls: dict = {}

    def fake_run(**kwargs):
        calls["run"] = kwargs
        return monitors._outcome("failed", reason="discovery_unavailable:ConnectError")

    def fake_record(outcome, **kwargs):
        calls["record"] = (outcome, kwargs)

    monkeypatch.setattr(chain, "run_refresh_monitors", fake_run)
    monkeypatch.setattr(chain, "record_refresh_monitors", fake_record)
    monkeypatch.setattr(chain, "_internal_headers", lambda *_a: {})
    monkeypatch.setenv("AGENT_RUNNER_TOKEN", "runner-token")

    summary = chain.run_monitors_after_refresh(ti=_Ti(), run_id="successfactors__dataset_refresh__abc")

    assert summary["status"] == "failed"
    assert calls["run"]["refresh_run_id"] == REFRESH
    assert calls["run"]["runner_token"] == "runner-token"
    assert calls["record"][1]["cartridge_id"] == "sap_successfactors"
    assert calls["record"][0]["status"] == "failed"


def test_console_invocation_payload_satisfies_the_scheduled_runner_contract():
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from airflow.dags.agent_runner_outcome import require_scheduled_invocation
    from app.services import scheduled_automation

    class _Conn:
        async def execute(self, *_args):
            return None

        async def fetchrow(self, *_args):
            return {
                "run_id": REFRESH,
                "cartridge_id": "sap_successfactors",
                "status": "success",
                "finished_at": datetime.now(timezone.utc),
            }

    agent = SimpleNamespace(
        id="44444444-4444-4444-8444-444444444444",
        slug="talent_monitor",
        cartridge_id="sap_successfactors",
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        is_active=True,
        extra={"role": "monitor", "monitor": {"wisdom_bit_id": "WB-TALENTO"}},
    )
    payload = asyncio.run(
        scheduled_automation.invoke_refresh_monitor(
            _Conn(),
            agent_id=agent.id,
            tenant_id=TENANT,
            workspace_id=WORKSPACE,
            refresh_run_id=REFRESH,
            airflow_dag_run_id="successfactors__dataset_refresh__abc",
            load_agent=AsyncMock(return_value=agent),
            reserve_scheduled_run=AsyncMock(
                return_value={"reserved": True, "duplicate": False, "id": 3, "status": "running", "fencing_token": 1}
            ),
            execute_reserved_scheduled_monitor=AsyncMock(
                return_value={
                    "run_id": 41,
                    "deterministic_monitor": True,
                    "monitor": {"status": "ok", "signals": 0, "blockers": 0, "engines": [], "alerted": False},
                }
            ),
        )
    )

    checked = require_scheduled_invocation(_Response(200, payload))
    assert checked == {"ok": True, "status": 200, "duplicate": False, "run_id": 41}
