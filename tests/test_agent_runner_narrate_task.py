from __future__ import annotations

from datetime import timedelta

import pytest

from tests.airflow_dag_test_loader import ROOT, load_dag, load_module

_PATH = "/api/operations/internal/control-room/narrate-alerts"
_SOURCE = (ROOT / "airflow/dags/agent_runner.py").read_text(encoding="utf-8")


class _Response:
    def __init__(self, status_code: int, payload: object):
        self.status_code = status_code
        self.payload = payload

    def json(self):
        return self.payload


def _dag(monkeypatch: pytest.MonkeyPatch):
    load_module(monkeypatch, "agent_runner_record", alias="agent_runner_record")
    return load_dag(monkeypatch, "agent_runner")


def test_narrate_task_is_a_sibling_of_record_run_so_run_state_stays_honest():
    # Airflow derives the DAG-run state from its leaf tasks. Narration after
    # record_run with trigger_rule=all_done would be the only leaf, and a green
    # narration would hide a failed record_run.
    assert "t_find >> t_inv >> [t_rec, t_narrate]" in _SOURCE
    assert "t_rec >> t_narrate" not in _SOURCE
    assert _SOURCE.count("t_find >> t_inv") == 1


def test_narrate_task_never_retries_and_runs_when_upstream_failed(monkeypatch):
    agent_runner = _dag(monkeypatch)
    task = agent_runner.t_narrate.kwargs

    assert task["task_id"] == "narrate_alerts"
    assert task["python_callable"] is agent_runner.narrate_alerts
    assert task["retries"] == 0
    assert task["trigger_rule"] == "all_done"
    assert task["execution_timeout"] < timedelta(minutes=agent_runner.INTERVAL_MIN)
    assert (
        timedelta(seconds=agent_runner.NARRATE_HTTP_TIMEOUT_SECONDS)
        < task["execution_timeout"]
    )
    # The monitor tasks keep the DAG defaults; only narration opts out.
    assert "retries" not in agent_runner.t_rec.kwargs
    assert "trigger_rule" not in agent_runner.t_rec.kwargs
    assert agent_runner.default_args["retries"] == 1


def test_narrate_task_calls_console_with_airflow_pair_key(monkeypatch):
    agent_runner = _dag(monkeypatch)
    calls: list[dict] = []
    key_names: list[str] = []

    def fake_key(name: str) -> str:
        key_names.append(name)
        return "airflow-pair-key"

    def fake_post(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return _Response(
            200,
            {
                "status": "ready",
                "workspaces": 2,
                "candidates": 3,
                "narrated_ready": 1,
                "narrated_template": 1,
                "skipped_current": 1,
                "budget_exhausted": 0,
                "lost_claim": 0,
                "deferred": 0,
                "failures": [],
            },
        )

    monkeypatch.setattr(agent_runner, "CONSOLE_URL", "http://console:8000/")
    monkeypatch.setattr(agent_runner, "_internal_key", fake_key)
    monkeypatch.setattr(agent_runner.requests, "post", fake_post)

    summary = agent_runner.narrate_alerts(run_id="scheduled__agent_runner")

    assert key_names == ["INTERNAL_API_KEY_AIRFLOW_TO_CONSOLE"]
    assert len(calls) == 1
    assert calls[0]["url"] == f"http://console:8000{_PATH}"
    assert calls[0]["headers"]["X-Api-Key"] == "airflow-pair-key"
    assert calls[0]["headers"]["X-Internal-Service"] == "airflow"
    assert calls[0]["timeout"] == agent_runner.NARRATE_HTTP_TIMEOUT_SECONDS
    assert summary["narrated_ready"] == 1
    assert summary["failures"] == 0
    assert summary["status"] == "ready"


@pytest.mark.parametrize("status_code", [302, 401, 403, 500, 503])
def test_narrate_task_fails_only_itself_on_non_2xx(monkeypatch, status_code):
    agent_runner = _dag(monkeypatch)
    monkeypatch.setattr(agent_runner, "_internal_key", lambda _name: "key")
    monkeypatch.setattr(
        agent_runner.requests,
        "post",
        lambda *_a, **_k: _Response(status_code, {"status": "ready"}),
    )

    with pytest.raises(RuntimeError, match="alert narration unavailable"):
        agent_runner.narrate_alerts()


def test_narrate_task_error_does_not_leak_provider_text(monkeypatch):
    agent_runner = _dag(monkeypatch)
    monkeypatch.setattr(agent_runner, "_internal_key", lambda _name: "key")

    def boom(*_a, **_k):
        raise ConnectionError("secret-host-detail sk-ant-leak")

    monkeypatch.setattr(agent_runner.requests, "post", boom)

    with pytest.raises(RuntimeError) as excinfo:
        agent_runner.narrate_alerts()
    assert "sk-ant" not in str(excinfo.value)
    assert "secret-host" not in str(excinfo.value)


def test_narrate_task_source_uses_new_path_and_pair_key():
    section = _SOURCE.split("def narrate_alerts", 1)[1].split("# ── DAG wiring", 1)[0]

    assert f'NARRATE_ALERTS_PATH = "{_PATH}"' in _SOURCE
    assert "NARRATE_ALERTS_PATH" in section
    assert '_internal_key("INTERNAL_API_KEY_AIRFLOW_TO_CONSOLE")' in section
    assert '"X-Internal-Service": "airflow"' in section
    assert "AGENT_RUNNER_TOKEN" not in section
    assert "RUNNER_TOKEN" not in section
    assert "trigger_rule=TriggerRule.ALL_DONE" in _SOURCE
    assert "from airflow.utils.trigger_rule import TriggerRule" in _SOURCE
