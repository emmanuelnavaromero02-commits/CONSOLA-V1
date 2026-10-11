from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from airflow.dags import cartridge_run_admission as admission
from airflow.dags import successfactors_daily_schedule as schedule
from airflow.dags.runtime_security_context import verify_runtime_signature
from tests.airflow_dag_test_loader import load_dag


KEY = "daily-successfactors-run-authority-key-with-entropy"
TENANT = "11111111-1111-4111-8111-111111111111"
WS_A = "22222222-2222-4222-8222-222222222222"
WS_B = "33333333-3333-4333-8333-333333333333"
DAY = "2026-10-09"


@pytest.fixture(autouse=True)
def _signing_key(monkeypatch):
    monkeypatch.setenv("SECURITY_CONTEXT_SIGNING_KEY", KEY)


class _Response:
    def __init__(self, status_code: int, payload: object = None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


class _Airflow:
    def __init__(
        self,
        *,
        active=None,
        list_status=200,
        trigger_status=200,
        unpause_status=200,
        paused=None,
    ):
        self.active = active or {}
        self.list_status = list_status
        self.trigger_status = trigger_status
        self.unpause_status = unpause_status
        self.paused = paused or {}
        self.triggered: list[dict] = []
        self.unpaused: list[str] = []

    def get(self, url, *, auth, timeout, params=None):
        dag_id = url.split("/dags/", 1)[1].split("/", 1)[0]
        if not url.endswith("/dagRuns"):
            return _Response(200, {"dag_id": dag_id, "is_paused": self.paused.get(dag_id, False)})
        assert ("state", "queued") in params and ("state", "running") in params
        if self.list_status != 200:
            return _Response(self.list_status)
        runs = [
            {"dag_run_id": "r", "state": "running", **run}
            if "conf" in run
            else {"dag_run_id": "r", "state": "running", "conf": run}
            for run in self.active.get(dag_id, [])
        ]
        return _Response(200, {"dag_runs": runs})

    def patch(self, url, *, auth, json, timeout):
        self.unpaused.append(url)
        return _Response(self.unpause_status)

    def post(self, url, *, auth, json, timeout):
        self.triggered.append({"url": url, **json})
        return _Response(self.trigger_status)


def _target(workspace: str = WS_A) -> dict[str, str]:
    return {"tenant_id": TENANT, "workspace_id": workspace, "conn_id": "tenant_sf"}


def test_dag_runs_daily_at_six_mexico_city_and_is_paused_on_creation(monkeypatch):
    monkeypatch.setenv("AIRFLOW__CORE__DAGS_ARE_PAUSED_AT_CREATION", "false")
    module = load_dag(monkeypatch, "successfactors_daily_extract")
    kwargs = module.dag.kwargs

    assert kwargs["dag_id"] == "sap_successfactors_daily_extract"
    assert kwargs["schedule_interval"] == "0 12 * * *"
    assert kwargs["is_paused_upon_creation"] is True
    assert kwargs["catchup"] is False and kwargs["max_active_runs"] == 1
    assert kwargs["start_date"].tzinfo is not None
    assert module.t_record.kwargs["trigger_rule"] == "all_done"
    for month in (1, 7):
        fire = datetime(2027, month, 15, 12, 0, tzinfo=timezone.utc)
        local = fire.astimezone(ZoneInfo("America/Mexico_City"))
        assert (local.hour, local.minute) == (6, 0)


def _scheduled(end: datetime, run_type: str = "scheduled") -> dict:
    return {"dag_run": SimpleNamespace(run_type=run_type), "data_interval_end": end}


def test_scheduled_day_is_the_mexico_city_calendar_day_of_the_interval():
    end = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)

    window = schedule.run_window(_scheduled(end), now=end + timedelta(minutes=3))

    assert window == {"day": DAY, "late": False}
    assert schedule.daily_run_id(DAY, WS_A) == f"sched_sf_daily_20261009_{WS_A}"


@pytest.mark.parametrize(
    "unpaused_at",
    [
        datetime(2026, 10, 10, 3, 0, tzinfo=timezone.utc),
        datetime(2026, 10, 10, 16, 30, tzinfo=timezone.utc),
    ],
)
def test_catch_up_run_fired_on_unpause_is_late_and_skipped(unpaused_at):
    last_closed = unpaused_at.replace(hour=12, minute=0)
    if last_closed > unpaused_at:
        last_closed -= timedelta(days=1)

    window = schedule.run_window(_scheduled(last_closed), now=unpaused_at)

    assert window["late"] is True
    assert schedule.run_outcome(
        {"status": "late_catchup", "targets": [], "day": window["day"]}, []
    ) == ("skipped", "late_catchup")


def test_manual_backdated_trigger_uses_the_real_day_and_reuses_its_run_ids():
    now = datetime(2026, 10, 9, 18, 0, tzinfo=timezone.utc)
    backdated = _scheduled(datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc), "manual")

    window = schedule.run_window(backdated, now=now)

    assert window == {"day": DAY, "late": False}
    airflow = _Airflow(trigger_status=409)
    results = schedule.trigger_targets(
        [_target(WS_A)], day=window["day"], base_url="http://a", username="u", password="p", http=airflow
    )
    assert airflow.triggered[0]["dag_run_id"] == schedule.daily_run_id(DAY, WS_A)
    assert results[0]["outcome"] == "duplicate"


def test_daily_conf_is_incremental_and_carries_run_bound_scheduled_authority():
    run_id = schedule.daily_run_id(DAY, WS_A)
    conf = schedule.daily_conf(_target(), day=DAY, run_id=run_id)

    assert (conf["target"], conf["mode"], conf["conn_id"]) == ("all", "incremental", "tenant_sf")
    assert conf["idempotency_key"] == f"sf-daily:{DAY}:{WS_A}"
    ctx = conf["security_context"]
    verify_runtime_signature(ctx)
    assert ctx["allowed_cartridges"] == ["sap_successfactors"]
    assert (ctx["dag_id"], ctx["dag_run_id"]) == ("sap_successfactors_extract_all", run_id)
    run = SimpleNamespace(
        dag_id="sap_successfactors_extract_all", run_id=run_id, queued_at=None, start_date=None
    )
    admitted = admission.admit_run(conf, cartridge_id="sap_successfactors", dag_run=run)
    assert (admitted.tenant_id, admitted.workspace_id) == (TENANT, WS_A)
    assert admitted.principal == "airflow:sap_successfactors_daily_extract"


def test_daily_authority_cannot_be_replayed_on_another_run_or_cartridge():
    conf = schedule.daily_conf(_target(), day=DAY, run_id="sched_sf_daily_a")
    other_run = SimpleNamespace(
        dag_id="sap_successfactors_extract_all", run_id="manual__x", queued_at=None, start_date=None
    )
    with pytest.raises(admission.RunAdmissionError):
        admission.admit_run(conf, cartridge_id="sap_successfactors", dag_run=other_run)
    same_run = SimpleNamespace(
        dag_id="sap_successfactors_extract_all", run_id="sched_sf_daily_a", queued_at=None, start_date=None
    )
    with pytest.raises(admission.RunAdmissionError):
        admission.admit_run(conf, cartridge_id="sap_b1", dag_run=same_run)


def test_targets_are_validated_and_deduplicated():
    payload = {
        "status": "ready",
        "targets": [_target(WS_B), _target(WS_A), {**_target(WS_A), "conn_id": "second"}],
    }
    assert [item["workspace_id"] for item in schedule.parse_targets(payload)] == [WS_A, WS_B]
    for bad in (
        {"status": "ready", "targets": [{**_target(), "workspace_id": "not-a-uuid"}]},
        {"status": "ready", "targets": [{**_target(), "conn_id": "bad conn"}]},
        {"status": "weird", "targets": []},
        {"status": "ready"},
    ):
        with pytest.raises(schedule.TargetDiscoveryError):
            schedule.parse_targets(bad)


def test_discovery_rejection_fails_closed():
    class _Http:
        def post(self, *_args, **_kwargs):
            return _Response(403, {"detail": "nope"})

    with pytest.raises(schedule.TargetDiscoveryError):
        schedule.discover_targets(console_url="http://console", headers={}, http=_Http())


def test_running_extraction_is_skipped_and_others_are_triggered_once_per_day():
    airflow = _Airflow(active={"sap_successfactors_extract": [{"workspace_id": WS_A}]})
    results = schedule.trigger_targets(
        [_target(WS_A), _target(WS_B)],
        day=DAY,
        base_url="http://airflow:8080",
        username="u",
        password="p",
        http=airflow,
    )

    assert results[0] == {"workspace_id": WS_A, "outcome": "already_running"}
    assert results[1]["outcome"] == "triggered"
    assert [item["dag_run_id"] for item in airflow.triggered] == [
        schedule.daily_run_id(DAY, WS_B)
    ]
    assert airflow.triggered[0]["url"].endswith("/dags/sap_successfactors_extract_all/dagRuns")
    assert airflow.triggered[0]["conf"]["workspace_id"] == WS_B


def test_rerun_on_the_same_day_is_idempotent():
    airflow = _Airflow(trigger_status=409)
    reserved: list[str] = []
    results = schedule.trigger_targets(
        [_target(WS_B)],
        day=DAY,
        base_url="http://a",
        username="u",
        password="p",
        http=airflow,
        reserve=lambda target, *, run_id, conf: reserved.append(run_id),
    )
    assert results[0]["outcome"] == "duplicate"
    assert reserved == []
    assert schedule.run_outcome({"status": "ready", "targets": [_target(WS_B)]}, results) == (
        "success",
        None,
    )


def test_unknown_or_unlistable_runs_never_trigger_a_second_extraction():
    unscoped = _Airflow(active={"sap_successfactors_extract_all": [{}]})
    results = schedule.trigger_targets(
        [_target(WS_A)], day=DAY, base_url="http://a", username="u", password="p", http=unscoped
    )
    assert results == [
        {"workspace_id": WS_A, "outcome": "failed", "stage": "unscoped_active_run"}
    ]
    assert unscoped.triggered == []

    broken = _Airflow(list_status=503)
    results = schedule.trigger_targets(
        [_target(WS_A)], day=DAY, base_url="http://a", username="u", password="p", http=broken
    )
    assert results[0]["outcome"] == "failed"
    assert broken.triggered == []


def test_stuck_runs_are_reported_as_failures_not_skipped_as_running():
    now = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)
    queued_long_ago = {
        "state": "queued",
        "logical_date": (now - timedelta(hours=7)).isoformat(),
        "conf": {"workspace_id": WS_A},
    }
    queued_recently = {
        "state": "queued",
        "logical_date": (now - timedelta(minutes=20)).isoformat(),
        "conf": {"workspace_id": WS_B},
    }
    airflow = _Airflow(active={"sap_successfactors_extract_all": [queued_long_ago, queued_recently]})
    results = schedule.trigger_targets(
        [_target(WS_A), _target(WS_B)],
        day=DAY,
        base_url="http://a",
        username="u",
        password="p",
        http=airflow,
        now=now,
    )
    assert results == [
        {
            "workspace_id": WS_A,
            "outcome": "blocked",
            "stage": "stale_active_run",
            "stale_runs": [
                {
                    "dag_id": "sap_successfactors_extract_all",
                    "dag_run_id": "r",
                    "state": "queued",
                    "dag_paused": False,
                    "age_hours": 7.0,
                }
            ],
        },
        {"workspace_id": WS_B, "outcome": "already_running"},
    ]
    assert airflow.triggered == [] and airflow.unpaused == []

    paused = _Airflow(
        active={"sap_successfactors_extract_all": [queued_recently]},
        paused={"sap_successfactors_extract_all": True},
    )
    results = schedule.trigger_targets(
        [_target(WS_B)], day=DAY, base_url="http://a", username="u", password="p", http=paused, now=now
    )
    assert [(item["outcome"], item["stage"]) for item in results] == [
        ("blocked", "stale_active_run")
    ]
    assert results[0]["stale_runs"][0]["dag_paused"] is True
    discovery = {"status": "ready", "targets": [_target(WS_B)]}
    assert schedule.run_outcome(discovery, results) == ("blocked", "stale_active_run")


def _running(hours: float, workspace: str | None) -> dict:
    now = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)
    return {
        "dag_run_id": f"run-{hours}",
        "state": "running",
        "start_date": (now - timedelta(hours=hours)).isoformat(),
        "conf": {"workspace_id": workspace} if workspace else {},
    }


def test_runs_running_for_more_than_twelve_hours_block_the_day_instead_of_skipping():
    now = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)
    airflow = _Airflow(
        active={
            "sap_successfactors_extract": [_running(30, WS_A)],
            "sap_successfactors_extract_all": [_running(3, WS_B)],
        }
    )
    results = schedule.trigger_targets(
        [_target(WS_A), _target(WS_B)],
        day=DAY,
        base_url="http://a",
        username="u",
        password="p",
        http=airflow,
        now=now,
    )

    assert results[0]["outcome"] == "blocked"
    assert results[0]["stale_runs"] == [
        {
            "dag_id": "sap_successfactors_extract",
            "dag_run_id": "run-30",
            "state": "running",
            "dag_paused": False,
            "age_hours": 30.0,
        }
    ]
    assert results[1] == {"workspace_id": WS_B, "outcome": "already_running"}
    assert airflow.triggered == []
    discovery = {"status": "ready", "targets": [_target(WS_A), _target(WS_B)]}
    assert schedule.run_outcome(discovery, results) == ("partial", "stale_active_run")
    assert schedule.STALE_RUNNING_AFTER == timedelta(hours=12)


def test_leftover_run_without_workspace_blocks_every_workspace_as_stuck():
    now = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)
    leftover = _Airflow(
        active={"sap_successfactors_extract_all": [_running(1, None)]},
        paused={"sap_successfactors_extract_all": True},
    )
    results = schedule.trigger_targets(
        [_target(WS_A), _target(WS_B)],
        day=DAY,
        base_url="http://a",
        username="u",
        password="p",
        http=leftover,
        now=now,
    )
    assert [(item["outcome"], item["stage"]) for item in results] == [
        ("blocked", "stale_active_run"),
        ("blocked", "stale_active_run"),
    ]
    assert leftover.triggered == [] and leftover.unpaused == []

    live = _Airflow(active={"sap_successfactors_extract_all": [_running(1, None)]})
    results = schedule.trigger_targets(
        [_target(WS_A)], day=DAY, base_url="http://a", username="u", password="p", http=live, now=now
    )
    assert results == [
        {"workspace_id": WS_A, "outcome": "failed", "stage": "unscoped_active_run"}
    ]


def test_triggered_extract_all_is_reserved_for_console_backpressure():
    airflow = _Airflow(trigger_status=200)
    reservations: list[tuple[str, dict]] = []
    results = schedule.trigger_targets(
        [_target(WS_A)],
        day=DAY,
        base_url="http://a",
        username="u",
        password="p",
        http=airflow,
        reserve=lambda target, *, run_id, conf: reservations.append((run_id, conf)),
    )
    run_id = schedule.daily_run_id(DAY, WS_A)
    assert results == [
        {"workspace_id": WS_A, "outcome": "triggered", "dag_run_id": run_id, "reserved": True}
    ]
    assert reservations[0][0] == run_id
    assert reservations[0][1]["idempotency_key"] == f"sf-daily:{DAY}:{WS_A}"

    def broken(target, *, run_id, conf):
        raise RuntimeError("registry down")

    results = schedule.trigger_targets(
        [_target(WS_A)], day=DAY, base_url="http://a", username="u", password="p", http=airflow, reserve=broken
    )
    assert results[0]["reserved"] is False
    discovery = {"status": "ready", "targets": [_target(WS_A)]}
    assert schedule.run_outcome(discovery, results) == ("partial", "reservation_failed")


def test_reservation_row_matches_the_console_extract_all_guard():
    now = datetime(2026, 10, 9, 12, 0, 5, tzinfo=timezone.utc)
    run_id = schedule.daily_run_id(DAY, WS_A)
    conf = schedule.daily_conf(_target(), day=DAY, run_id=run_id)
    saved: list[dict] = []

    class _Mcp:
        def post(self, url, *, headers, json, timeout):
            saved.append(json)
            return _Response(200, {"result": {"saved": True, "status": "queued"}})

    schedule.reserve_extract_all(
        _target(), run_id=run_id, conf=conf, mcp_url="http://mcp:8010/", headers={}, http=_Mcp(), now=now
    )
    args = saved[0]["args"]
    assert (args["dag_id"], args["entity"], args["status"]) == (
        "sap_successfactors_extract_all",
        "__extract_all__",
        "queued",
    )
    assert args["run_id"] == args["airflow_dag_run_id"] == run_id
    assert (args["tenant_id"], args["workspace_id"]) == (TENANT, WS_A)
    assert args["started_at"] == now.isoformat()
    assert saved[0]["security_context"]["run_id"] == run_id

    class _Rejecting:
        def post(self, *_args, **_kwargs):
            return _Response(403, {"detail": "nope"})

    with pytest.raises(RuntimeError):
        schedule.reserve_extract_all(
            _target(), run_id=run_id, conf=conf, mcp_url="http://mcp", headers={}, http=_Rejecting()
        )


def test_run_outcome_is_never_silently_ok():
    discovery = {"status": "ready", "targets": [_target(WS_A), _target(WS_B)]}
    ok = {"workspace_id": WS_A, "outcome": "triggered"}
    bad = {"workspace_id": WS_B, "outcome": "failed"}
    running = {"workspace_id": WS_B, "outcome": "already_running"}

    assert schedule.run_outcome(None, []) == ("failed", "discovery_failed")
    assert schedule.run_outcome(discovery, [ok, bad]) == ("partial", "workspaces_failed")
    assert schedule.run_outcome(discovery, [bad, bad]) == ("failed", "workspaces_failed")
    assert schedule.run_outcome(discovery, []) == ("failed", "workspaces_failed")
    assert schedule.run_outcome({"status": "ready", "targets": []}, []) == (
        "skipped",
        "no_successfactors_connection",
    )
    assert schedule.run_outcome(
        {"status": "partial", "targets": [_target()], "scope_failures": 1}, [ok]
    ) == ("partial", "scope_discovery_partial")
    only_running = {"status": "ready", "targets": [_target(WS_B)]}
    assert schedule.run_outcome(only_running, [running]) == ("skipped", "already_running")
    assert schedule.run_outcome(discovery, [ok, running]) == ("success", None)
    stuck = {"workspace_id": WS_B, "outcome": "blocked", "stage": "stale_active_run"}
    assert schedule.run_outcome(only_running, [stuck]) == ("blocked", "stale_active_run")
    assert schedule.run_outcome(discovery, [ok, stuck]) == ("partial", "stale_active_run")
    assert schedule.run_outcome(discovery, [bad, stuck]) == ("failed", "workspaces_failed")


class _Ti:
    def __init__(self, discovery, invocation):
        self.discovery = discovery
        self.invocation = invocation

    def xcom_pull(self, *, task_ids: str, key: str | None = None):
        if task_ids == "discover_targets":
            return self.discovery
        return self.invocation


@pytest.mark.parametrize(
    "discovery,invocation,expected,reason",
    [
        (
            {"status": "ready", "targets": [], "scope_failures": 0, "day": DAY},
            {"results": []},
            "skipped",
            "no_successfactors_connection",
        ),
        (
            {"status": "late_catchup", "targets": [], "scope_failures": 0, "day": DAY},
            {"results": []},
            "skipped",
            "late_catchup",
        ),
        (
            {"status": "ready", "targets": [_target()], "scope_failures": 0, "day": DAY},
            {"results": [{"workspace_id": WS_A, "outcome": "triggered", "reserved": True}]},
            "success",
            None,
        ),
        (None, None, "failed", "discovery_failed"),
        (
            {"status": "ready", "targets": [_target()], "scope_failures": 0, "day": DAY},
            {"results": [{"workspace_id": WS_A, "outcome": "blocked", "stage": "stale_active_run"}]},
            "blocked",
            "stale_active_run",
        ),
    ],
)
def test_record_run_saves_the_outcome_like_other_scheduled_runs(
    monkeypatch, discovery, invocation, expected, reason
):
    module = load_dag(monkeypatch, "successfactors_daily_extract")
    saved: list[dict] = []

    def post(_url, *, headers, json, timeout):
        saved.append(json)
        return _Response(200, {"result": {"saved": True, "status": json["args"]["status"]}})

    monkeypatch.setattr(module.requests, "post", post)
    monkeypatch.setattr(module, "_headers", lambda _target: {})
    context = {
        "ti": _Ti(discovery, invocation),
        "run_id": "scheduled__2026-10-08T12:00:00+00:00",
        "logical_date": datetime(2026, 10, 8, 12, tzinfo=timezone.utc),
        "data_interval_end": datetime(2026, 10, 9, 12, tzinfo=timezone.utc),
    }
    if expected in {"failed", "blocked"}:
        with pytest.raises(RuntimeError, match=f"finished {expected}"):
            module.record_run(**context)
    else:
        assert module.record_run(**context) == expected
    args = saved[0]["args"]
    assert args["status"] == expected
    assert (args["cartridge_id"], args["entity"]) == ("platform", "SuccessFactorsDailyExtract")
    if discovery is not None:
        assert args["extra"]["day"] == DAY
    assert args["extra"]["reason"] == reason
    assert saved[0]["security_context"]["purpose"] == "mcp.pipeline_run_save"
    if reason == "stale_active_run":
        assert args["error_message"] == "Bloqueada por corrida atascada de SuccessFactors"
    else:
        assert args.get("error_message") == reason


def test_late_catch_up_run_never_calls_discovery(monkeypatch):
    module = load_dag(monkeypatch, "successfactors_daily_extract")
    pushed: dict = {}

    class _PushTi:
        def xcom_push(self, *, key, value):
            pushed[key] = value

    def forbidden(**_kwargs):
        raise AssertionError("late catch-up must not discover targets")

    monkeypatch.setattr(module, "_discover_targets", forbidden)
    context = {
        "ti": _PushTi(),
        "dag_run": SimpleNamespace(run_type="scheduled"),
        "data_interval_end": datetime(2020, 1, 1, 12, tzinfo=timezone.utc),
    }

    assert module.discover_targets(**context) == 0
    assert pushed["discovery"]["status"] == "late_catchup"
    assert pushed["discovery"]["day"] == "2020-01-01"
