from __future__ import annotations

import asyncio
import importlib
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi import HTTPException

from tests.test_mcp_domain_kpi_tools import (
    TENANT,
    WORKSPACE,
    _load_mcp_module,
    _purge_app_modules,
    _signed,
)


BASE = "http://airflow:8080/airflow/api/v1"


@pytest.fixture(autouse=True)
def _clean_imports():
    saved = list(sys.path)
    yield
    sys.path[:] = saved
    _purge_app_modules()


class FakeResponse:
    def __init__(self, status_code: int = 200, payload: Any = None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = ""

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError(f"unexpected HTTP {self.status_code}")


class FakeAirflow:
    def __init__(self, routes: dict[tuple[str, str], FakeResponse]):
        self.routes = routes
        self.requests: list[tuple[str, str, dict[str, Any]]] = []

    def client(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def request(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs))
        response = self.routes.get((method, url))
        if response is None:
            raise AssertionError(f"unexpected {method} {url}")
        if callable(response):
            return response(kwargs)
        return response


def _tools(monkeypatch, routes):
    _load_mcp_module(monkeypatch, "app.main")
    airflow = importlib.import_module("app.tools.airflow")
    monkeypatch.setattr(airflow, "_BASE", "http://airflow:8080/airflow")
    fake = FakeAirflow(routes)
    monkeypatch.setattr(airflow, "_client", fake.client)
    return airflow, fake


def _dag(**overrides):
    dag = {
        "dag_id": "sap_successfactors_extract_all",
        "is_paused": True,
        "is_active": True,
        "has_import_errors": False,
        "schedule_interval": None,
        "timetable_description": "Never, external triggers only",
        "max_active_runs": 1,
    }
    dag.update(overrides)
    return dag


def _iso(delta: timedelta) -> str:
    return (datetime.now(timezone.utc) - delta).isoformat()


def _runs_by_state(pages: dict[str, dict]):
    """Serve one dagRuns page per requested state, recording the query."""

    def respond(kwargs):
        params = kwargs.get("params") or []
        states = [value for key, value in params if key == "state"]
        assert len(states) == 1, params
        assert ("order_by", "execution_date") in params
        return FakeResponse(200, pages.get(states[0], {"dag_runs": [], "total_entries": 0}))

    return respond


def test_describe_reports_manual_schedule_scheduler_health_and_stale_runs(monkeypatch):
    dag_url = f"{BASE}/dags/sap_successfactors_extract_all"
    airflow, fake = _tools(
        monkeypatch,
        {
            ("GET", dag_url): FakeResponse(200, _dag()),
            ("GET", f"{BASE}/health"): FakeResponse(200, {"scheduler": {"status": "healthy"}}),
            ("GET", f"{dag_url}/dagRuns"): _runs_by_state(
                {
                    "queued": {
                        "dag_runs": [
                            {"dag_run_id": "old", "state": "queued", "queued_at": _iso(timedelta(hours=3)), "conf": {"tenant_id": TENANT, "workspace_id": WORKSPACE, "cartridge_id": "sap_successfactors", "security_context": {"_signature": "secret"}}},
                            {"dag_run_id": "new", "state": "queued", "queued_at": _iso(timedelta(minutes=2)), "conf": {"tenant_id": TENANT}},
                        ],
                        "total_entries": 2,
                    },
                    "running": {
                        "dag_runs": [
                            {"dag_run_id": "stuck", "state": "running", "execution_date": _iso(timedelta(hours=5)), "start_date": _iso(timedelta(hours=4)), "conf": {}},
                        ],
                        "total_entries": 1,
                    },
                }
            ),
        },
    )
    result = asyncio.run(airflow.airflow_describe_dag("sap_successfactors_extract_all"))
    assert result["found"] is True
    assert result["is_paused"] is True
    assert result["schedule_kind"] == "manual"
    assert result["scheduler_healthy"] is True
    by_id = {run["dag_run_id"]: run for run in result["runs"]}
    assert (by_id["old"]["stale"], by_id["new"]["stale"], by_id["stuck"]["stale"]) == (True, False, True)
    assert "security_context" not in by_id["old"]["conf"]
    assert by_id["old"]["conf"] == {"tenant_id": TENANT, "workspace_id": WORKSPACE, "cartridge_id": "sap_successfactors"}
    assert (result["running_truncated"], result["queued_truncated"], result["runs_truncated"]) == (False, False, False)
    list_calls = [call for call in fake.requests if call[1].endswith("/dagRuns")]
    assert [dict(call[2]["params"])["state"] for call in list_calls] == ["running", "queued"]
    assert all(method == "GET" for method, _url, _kwargs in fake.requests)


def test_running_runs_are_never_paged_out_by_a_queued_flood(monkeypatch):
    dag_url = f"{BASE}/dags/sap_successfactors_extract"
    flood = [
        {"dag_run_id": f"q{i}", "state": "queued", "execution_date": _iso(timedelta(minutes=200 - i)), "conf": {}}
        for i in range(100)
    ]
    airflow, _fake = _tools(
        monkeypatch,
        {
            ("GET", dag_url): FakeResponse(200, _dag(dag_id="sap_successfactors_extract", is_paused=False)),
            ("GET", f"{BASE}/health"): FakeResponse(200, {"scheduler": {"status": "healthy"}}),
            ("GET", f"{dag_url}/dagRuns"): _runs_by_state(
                {
                    "queued": {"dag_runs": flood, "total_entries": 150},
                    "running": {"dag_runs": [{"dag_run_id": "worker", "state": "running", "execution_date": _iso(timedelta(hours=6)), "conf": {}}], "total_entries": 1},
                }
            ),
        },
    )
    result = asyncio.run(airflow.airflow_describe_dag("sap_successfactors_extract"))
    assert "worker" in {run["dag_run_id"] for run in result["runs"] if run["state"] == "running"}
    assert result["queued_truncated"] is True
    assert result["running_truncated"] is False
    assert result["runs_truncated"] is True


def test_describe_falls_back_to_the_logical_date_when_queued_at_is_absent(monkeypatch):
    dag_url = f"{BASE}/dags/sap_successfactors_extract"
    airflow, _fake = _tools(
        monkeypatch,
        {
            ("GET", dag_url): FakeResponse(200, _dag(dag_id="sap_successfactors_extract")),
            ("GET", f"{BASE}/health"): FakeResponse(200, {"scheduler": {"status": "unhealthy"}}),
            ("GET", f"{dag_url}/dagRuns"): _runs_by_state(
                {
                    "queued": {
                        "dag_runs": [
                            {"dag_run_id": "old", "state": "queued", "logical_date": _iso(timedelta(hours=2)), "execution_date": _iso(timedelta(hours=2))},
                            {"dag_run_id": "fresh", "state": "queued", "execution_date": _iso(timedelta(minutes=1))},
                            {"dag_run_id": "undated", "state": "queued"},
                        ],
                        "total_entries": 3,
                    },
                    "running": {"dag_runs": [], "total_entries": 120},
                }
            ),
        },
    )
    result = asyncio.run(airflow.airflow_describe_dag("sap_successfactors_extract"))
    assert [(run["dag_run_id"], run["stale"]) for run in result["runs"]] == [("old", True), ("fresh", False), ("undated", False)]
    assert result["runs"][0]["queued_at"]
    assert result["scheduler_healthy"] is False
    assert result["running_truncated"] is True
    assert result["runs_truncated"] is True


@pytest.mark.parametrize(
    "overrides,kind",
    [
        ({"schedule_interval": {"__type": "CronExpression", "value": "*/5 * * * *"}, "timetable_description": "Every 5 minutes"}, "scheduled"),
        ({"schedule_interval": None, "timetable_description": "Triggered by datasets"}, "scheduled"),
        ({"schedule_interval": None, "timetable_summary": "None", "timetable_description": "Never, external triggers only"}, "manual"),
        ({"schedule_interval": None, "timetable_summary": "None", "timetable_description": None}, "scheduled"),
        ({"schedule_interval": None, "timetable_description": ""}, "scheduled"),
        ({"schedule_interval": None, "timetable_description": "never, external triggers only (legacy)"}, "scheduled"),
        ({"schedule_interval": None, "timetable_summary": "0 * * * *"}, "scheduled"),
        ({"schedule_interval": {"__type": "TimeDelta", "days": 1, "seconds": 0, "microseconds": 0}, "timetable_description": ""}, "scheduled"),
        ({"schedule_interval": {"__type": "RelativeDelta", "months": 1}, "timetable_description": None}, "scheduled"),
        ({"schedule_interval": "@daily", "timetable_description": "Never, external triggers only"}, "scheduled"),
    ],
)
def test_schedule_kind_only_calls_external_trigger_dags_manual(monkeypatch, overrides, kind):
    airflow, _fake = _tools(monkeypatch, {})
    assert airflow._schedule_kind(_dag(**overrides)) == kind


def test_describe_missing_dag_is_not_an_error(monkeypatch):
    airflow, _fake = _tools(monkeypatch, {("GET", f"{BASE}/dags/sap_b1_extract"): FakeResponse(404)})
    result = asyncio.run(airflow.airflow_describe_dag("sap_b1_extract"))
    assert result["found"] is False
    assert result["runs"] == []


def test_unpause_resumes_only_paused_manual_dags(monkeypatch):
    dag_url = f"{BASE}/dags/sap_successfactors_extract"
    airflow, fake = _tools(
        monkeypatch,
        {
            ("GET", dag_url): FakeResponse(200, _dag(dag_id="sap_successfactors_extract")),
            ("PATCH", dag_url): FakeResponse(200, _dag(dag_id="sap_successfactors_extract", is_paused=False)),
        },
    )
    result = asyncio.run(airflow.airflow_unpause_manual_dag("sap_successfactors_extract"))
    assert result == {"dag_id": "sap_successfactors_extract", "unpaused": True, "was_paused": True, "reason": "unpaused"}
    method, _url, kwargs = fake.requests[-1]
    assert method == "PATCH"
    assert kwargs["json"] == {"is_paused": False}
    assert kwargs["params"] == {"update_mask": "is_paused"}


@pytest.mark.parametrize(
    "dag,reason",
    [
        (_dag(schedule_interval={"__type": "CronExpression", "value": "*/10 * * * *"}, timetable_description="Every 10 minutes"), "not_manual"),
        (_dag(is_paused=False), "already_unpaused"),
        (_dag(is_active=False), "dag_inactive"),
        (_dag(has_import_errors=True), "dag_inactive"),
    ],
)
def test_unpause_refuses_scheduled_inactive_or_running_dags_without_patch(monkeypatch, dag, reason):
    dag_url = f"{BASE}/dags/sap_successfactors_extract_all"
    airflow, fake = _tools(monkeypatch, {("GET", dag_url): FakeResponse(200, dag)})
    result = asyncio.run(airflow.airflow_unpause_manual_dag("sap_successfactors_extract_all"))
    assert result["unpaused"] is False
    assert result["reason"] == reason
    assert [method for method, _url, _kwargs in fake.requests] == ["GET"]


def test_mark_failed_patches_only_when_state_still_matches(monkeypatch):
    run_url = f"{BASE}/dags/sap_successfactors_extract/dagRuns/manual__1"
    airflow, fake = _tools(
        monkeypatch,
        {
            ("GET", run_url): FakeResponse(200, {"state": "queued"}),
            ("PATCH", run_url): FakeResponse(200, {"state": "failed"}),
        },
    )
    result = asyncio.run(airflow.airflow_mark_dag_run_failed("sap_successfactors_extract", "manual__1", ["queued"]))
    assert result["marked"] is True
    assert result["previous_state"] == "queued"
    assert fake.requests[-1][0] == "PATCH"
    assert fake.requests[-1][2]["json"] == {"state": "failed"}


def test_mark_failed_reports_state_change_without_patch(monkeypatch):
    run_url = f"{BASE}/dags/sap_successfactors_extract/dagRuns/manual__1"
    airflow, fake = _tools(monkeypatch, {("GET", run_url): FakeResponse(200, {"state": "success"})})
    result = asyncio.run(airflow.airflow_mark_dag_run_failed("sap_successfactors_extract", "manual__1", ["queued", "running"]))
    assert result == {"dag_id": "sap_successfactors_extract", "dag_run_id": "manual__1", "marked": False, "found": True, "state": "success", "reason": "state_changed"}
    assert [method for method, _url, _kwargs in fake.requests] == ["GET"]


def test_mark_failed_missing_run_is_not_found(monkeypatch):
    run_url = f"{BASE}/dags/sap_successfactors_extract/dagRuns/manual__gone"
    airflow, _fake = _tools(monkeypatch, {("GET", run_url): FakeResponse(404)})
    result = asyncio.run(airflow.airflow_mark_dag_run_failed("sap_successfactors_extract", "manual__gone", ["queued"]))
    assert result["found"] is False
    assert result["marked"] is False


@pytest.mark.parametrize("states", [[], ["success"], ["queued", "failed"], ["up_for_retry"]])
def test_mark_failed_never_touches_terminal_or_unknown_states(monkeypatch, states):
    airflow, fake = _tools(monkeypatch, {})
    with pytest.raises(ValueError):
        asyncio.run(airflow.airflow_mark_dag_run_failed("sap_successfactors_extract", "manual__1", states))
    assert fake.requests == []


def test_task_instances_and_dag_list_expose_additive_fields(monkeypatch):
    ti_url = f"{BASE}/dags/sap_successfactors_extract/dagRuns/manual__1/taskInstances"
    airflow, _fake = _tools(
        monkeypatch,
        {
            ("GET", ti_url): FakeResponse(200, {"task_instances": [{"task_id": "extract", "state": "running", "duration": 3, "start_date": "2026-09-26T10:00:00+00:00", "end_date": None}]}),
            ("GET", f"{BASE}/dags"): FakeResponse(200, {"dags": [_dag(), _dag(dag_id="entity_scheduler", is_paused=False, schedule_interval={"__type": "CronExpression", "value": "*/5 * * * *"}, timetable_description="Every 5 minutes")]}),
        },
    )
    tasks = asyncio.run(airflow.airflow_list_task_instances("sap_successfactors_extract", "manual__1"))
    assert tasks["tasks"][0]["start_date"] == "2026-09-26T10:00:00+00:00"
    assert tasks["tasks"][0]["end_date"] is None
    dags = asyncio.run(airflow.airflow_list_dags())["dags"]
    assert [dag["schedule_kind"] for dag in dags] == ["manual", "scheduled"]
    assert dags[1]["timetable_description"] == "Every 5 minutes"


def _pipeline_ctx(permissions, **overrides):
    ctx = {
        "trusted": True,
        "source": "console",
        "role": "analyst",
        "email": "ops@omega.local",
        "tenant_id": TENANT,
        "workspace_id": WORKSPACE,
        "permissions": permissions,
        "allowed_cartridges": ["sap_successfactors"],
    }
    ctx.update(overrides)
    return _signed(ctx)


def _enforce(main, tool, args, ctx):
    req = main.InvokeRequest(tool=tool, args=args, security_context=ctx)
    return main._enforce_data_scope(req, "console"), req


def test_new_tools_require_the_right_pipeline_permissions(monkeypatch):
    main = _load_mcp_module(monkeypatch, "app.main")
    reader = _pipeline_ctx(["pipelines.read"])
    _enforce(main, "airflow_describe_dag", {"dag_id": "sap_successfactors_extract"}, reader)
    for tool, args in (
        ("airflow_unpause_manual_dag", {"dag_id": "sap_successfactors_extract"}),
        ("airflow_mark_dag_run_failed", {"dag_id": "sap_successfactors_extract", "dag_run_id": "manual__1", "expected_states": ["queued"]}),
    ):
        with pytest.raises(HTTPException) as exc:
            _enforce(main, tool, args, reader)
        assert exc.value.status_code == 403
        assert "pipelines.run" in str(exc.value.detail)


def test_unpause_refuses_shared_platform_and_foreign_cartridge_dags(monkeypatch):
    main = _load_mcp_module(monkeypatch, "app.main")
    runner = _pipeline_ctx(["pipelines.read", "pipelines.run"])
    _enforce(main, "airflow_unpause_manual_dag", {"dag_id": "sap_successfactors_extract_all"}, runner)
    monkeypatch.setattr(main, "_registered_cartridges_for_dag", lambda _dag_id: set())
    for dag_id in ("entity_scheduler", "agent_runner", "dataset_refresh_chain", "replicon_extract"):
        with pytest.raises(HTTPException) as exc:
            _enforce(main, "airflow_unpause_manual_dag", {"dag_id": dag_id}, runner)
        assert exc.value.status_code == 403, dag_id
    unscoped = _pipeline_ctx(["pipelines.read", "pipelines.run"], tenant_id=None, workspace_id=None)
    with pytest.raises(HTTPException):
        _enforce(main, "airflow_unpause_manual_dag", {"dag_id": "sap_successfactors_extract"}, unscoped)


def test_mark_failed_requires_the_run_inside_the_caller_scope(monkeypatch):
    main = _load_mcp_module(monkeypatch, "app.main")
    runner = _pipeline_ctx(["pipelines.read", "pipelines.run"])
    checked: list[str] = []

    def run_scope(ctx, run_id):
        checked.append(run_id)
        if run_id != "manual__mine":
            raise HTTPException(403, detail="run_id not found or not allowed")

    monkeypatch.setattr(main, "_require_pipeline_run_scope", run_scope)
    _enforce(main, "airflow_mark_dag_run_failed", {"dag_id": "sap_successfactors_extract", "dag_run_id": "manual__mine", "expected_states": ["queued"]}, runner)
    with pytest.raises(HTTPException):
        _enforce(main, "airflow_mark_dag_run_failed", {"dag_id": "sap_successfactors_extract", "dag_run_id": "manual__foreign", "expected_states": ["queued"]}, runner)
    with pytest.raises(HTTPException):
        _enforce(main, "airflow_mark_dag_run_failed", {"dag_id": "sap_successfactors_extract", "expected_states": ["queued"]}, runner)
    with pytest.raises(HTTPException):
        _enforce(main, "airflow_mark_dag_run_failed", {"dag_id": "dataset_refresh_chain", "dag_run_id": "manual__mine", "expected_states": ["running"]}, runner)
    assert checked == ["manual__mine", "manual__foreign"]


def test_describe_splits_own_runs_from_foreign_backlog_counts(monkeypatch):
    main = _load_mcp_module(monkeypatch, "app.main")
    reader = _pipeline_ctx(["pipelines.read"])
    payload = {
        "dag_id": "sap_successfactors_extract_all",
        "found": True,
        "runs": [
            {"dag_run_id": "mine", "state": "queued", "stale": True, "conf": {"tenant_id": TENANT, "workspace_id": WORKSPACE, "cartridge_id": "sap_successfactors", "mode": "incremental"}},
            {"dag_run_id": "theirs", "state": "queued", "stale": True, "conf": {"tenant_id": "33333333-3333-3333-3333-333333333333", "workspace_id": "44444444-4444-4444-4444-444444444444"}},
            {"dag_run_id": "orphan", "state": "running", "stale": False, "conf": {}},
            {"dag_run_id": "cartridge_only", "state": "running", "stale": True, "conf": {"cartridge_id": "sap_successfactors"}},
            {"dag_run_id": "tenant_only", "state": "queued", "stale": False, "conf": {"tenant_id": TENANT, "cartridge_id": "sap_successfactors"}},
            {"dag_run_id": "other_workspace", "state": "running", "stale": True, "conf": {"tenant_id": TENANT, "workspace_id": "55555555-5555-5555-5555-555555555555"}},
        ],
        "running_truncated": False,
        "foreign": {"queued": 0, "running": 0, "stale_queued": 0, "stale_running": 0},
    }
    filtered = main._filter_airflow_payload("airflow_describe_dag", payload, reader)
    assert [run["dag_run_id"] for run in filtered["runs"]] == ["mine"]
    assert filtered["runs"][0]["conf"] == {"tenant_id": TENANT, "workspace_id": WORKSPACE, "mode": "incremental"}
    assert filtered["foreign"] == {"queued": 2, "running": 3, "stale_queued": 1, "stale_running": 2}
    assert filtered["running_truncated"] is False
    for hidden in ("theirs", "33333333", "cartridge_only", "tenant_only", "other_workspace", "55555555"):
        assert hidden not in str(filtered)


def test_describe_treats_nothing_as_own_without_a_full_scope(monkeypatch):
    main = _load_mcp_module(monkeypatch, "app.main")
    payload = {
        "runs": [
            {"dag_run_id": "scoped", "state": "running", "stale": True, "conf": {"tenant_id": TENANT, "workspace_id": WORKSPACE}},
        ],
    }
    unscoped = _pipeline_ctx(["pipelines.read"], tenant_id=None, workspace_id=None)
    filtered = main._filter_airflow_payload("airflow_describe_dag", payload, unscoped)
    assert filtered["runs"] == []
    assert filtered["foreign"]["stale_running"] == 1
    foreign_cartridge = _pipeline_ctx(["pipelines.read"], allowed_cartridges=["replicon"])
    payload["runs"][0]["conf"]["cartridge_id"] = "sap_successfactors"
    filtered = main._filter_airflow_payload("airflow_describe_dag", payload, foreign_cartridge)
    assert filtered["runs"] == []
