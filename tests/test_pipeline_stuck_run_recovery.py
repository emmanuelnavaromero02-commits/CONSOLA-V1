from __future__ import annotations

import asyncio
import importlib
import json
import re
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException


TENANT = "11111111-1111-1111-1111-111111111111"
WORKSPACE = "22222222-2222-2222-2222-222222222222"
NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)


def _recovery():
    return importlib.import_module("app.domains.pipeline.stuck_run_recovery")


def _service():
    return importlib.import_module("app.domains.pipeline.stuck_run_recovery_service")


def _row(**overrides):
    row = {
        "run_id": "manual__run-1",
        "dag_id": "sap_successfactors_extract",
        "cartridge_id": "sap_successfactors",
        "entity": "User",
        "airflow_dag_run_id": "manual__run-1",
        "status": "queued",
        "started_at": NOW - timedelta(minutes=30),
        "finished_at": None,
        "error_message": None,
        "extra": {},
        "fencing_token": 3,
        "lease_expires_at": None,
        "tenant_id": TENANT,
        "workspace_id": WORKSPACE,
    }
    row.update(overrides)
    return row


def _truth(**overrides):
    values = {
        "found": True,
        "state": "queued",
        "queued_at": NOW - timedelta(minutes=30),
        "start_date": None,
        "end_date": None,
        "active_tasks": None,
        "last_task_activity_at": None,
        "dag_found": True,
        "dag_paused": False,
        "schedule_kind": "manual",
        "sibling_running": False,
        "scheduler_healthy": True,
        "error": None,
    }
    values.update(overrides)
    return _recovery().AirflowRunTruth(**values)


CLASSIFIER_MATRIX = [
    ("terminal_row", {"status": "success"}, {}, "not_applicable", "none", False),
    ("sync_now_aggregate", {"dag_id": "sync_now", "entity": "__sync_now__"}, {}, "not_applicable", "none", False),
    ("no_airflow_run", {"airflow_dag_run_id": None}, {}, "not_applicable", "none", False),
    ("lease_governed", {"dag_id": "dataset_refresh_chain", "lease_expires_at": NOW}, {}, "not_applicable", "none", False),
    ("airflow_unreachable", {}, {"error": "ConnectError"}, "unverifiable", "none", False),
    ("airflow_success_recent", {"started_at": NOW - timedelta(minutes=1)}, {"state": "success"}, "airflow_terminal", "sync_terminal", False),
    ("airflow_failed", {}, {"state": "failed"}, "airflow_terminal", "sync_terminal", False),
    ("missing_run_known_dag", {}, {"found": False, "state": "not_found"}, "missing_in_airflow", "mark_failed", False),
    ("missing_run_unknown_dag", {}, {"found": False, "state": "not_found", "dag_found": None}, "unverifiable", "none", False),
    ("missing_run_dag_absent", {}, {"found": False, "state": "not_found", "dag_found": False}, "unverifiable", "none", False),
    ("reserved_missing_after_grace", {"started_at": NOW - timedelta(minutes=3), "extra": {"reserved": True}}, {"found": False, "state": "not_found"}, "missing_in_airflow", "mark_failed", False),
    ("reserved_missing_within_grace", {"started_at": NOW - timedelta(seconds=60), "extra": json.dumps({"reserved": True})}, {"found": False, "state": "not_found"}, "too_recent", "none", False),
    ("unreserved_missing_recent", {"started_at": NOW - timedelta(minutes=5)}, {"found": False, "state": "not_found"}, "too_recent", "none", False),
    ("queued_recent", {"started_at": NOW - timedelta(minutes=5)}, {}, "too_recent", "none", False),
    ("queued_paused_dag", {}, {"dag_paused": True}, "stalled_queued_paused_dag", "mark_failed", True),
    ("queued_waiting_turn", {}, {"sibling_running": True}, "waiting_turn", "none", False),
    ("queued_no_progress", {}, {}, "stalled_queued_no_progress", "mark_failed", True),
    ("queued_requeued_recently", {}, {"queued_at": NOW - timedelta(minutes=2)}, "too_recent", "none", False),
    ("queued_scheduler_unhealthy", {}, {"scheduler_healthy": False}, "unverifiable", "none", False),
    ("queued_scheduler_unknown", {}, {"scheduler_healthy": None}, "unverifiable", "none", False),
    ("queued_sibling_unknown", {}, {"sibling_running": None}, "unverifiable", "none", False),
    ("queued_pause_unknown", {}, {"dag_paused": None}, "unverifiable", "none", False),
    ("running_with_tasks", {"status": "running"}, {"state": "running", "active_tasks": 2}, "live", "none", False),
    ("running_between_tasks", {"status": "running"}, {"state": "running", "active_tasks": 0, "last_task_activity_at": NOW - timedelta(minutes=4)}, "live", "none", False),
    ("running_no_tasks", {"status": "running"}, {"state": "running", "active_tasks": 0, "last_task_activity_at": NOW - timedelta(minutes=40)}, "stalled_running_no_tasks", "mark_failed", True),
    ("running_no_tasks_unhealthy", {"status": "running"}, {"state": "running", "active_tasks": 0, "last_task_activity_at": NOW - timedelta(minutes=40), "scheduler_healthy": False}, "unverifiable", "none", False),
    ("running_tasks_unknown", {"status": "running"}, {"state": "running", "active_tasks": None}, "unverifiable", "none", False),
    ("unknown_run_state", {}, {"state": "restarting"}, "unverifiable", "none", False),
]


@pytest.mark.parametrize(
    "case,row_overrides,truth_overrides,classification,action,neutralize",
    CLASSIFIER_MATRIX,
    ids=[case[0] for case in CLASSIFIER_MATRIX],
)
def test_classifier_matrix(case, row_overrides, truth_overrides, classification, action, neutralize):
    recovery = _recovery()
    verdict = recovery.classify_stuck_run(
        _row(**row_overrides), _truth(**truth_overrides), now=NOW
    )
    assert verdict.classification == classification, case
    assert verdict.action == action, case
    assert verdict.neutralize_airflow is neutralize, case
    assert verdict.reason_es
    if classification == "unverifiable":
        assert verdict.certain is False


def test_missing_truth_never_marks_a_run_failed():
    recovery = _recovery()
    verdict = recovery.classify_stuck_run(_row(started_at=NOW - timedelta(days=9)), None, now=NOW)
    assert verdict.classification == "unverifiable"
    assert verdict.action == "none"


def test_time_alone_never_fails_a_run():
    """A very old row is only failed when Airflow itself explains why."""
    recovery = _recovery()
    ancient = _row(started_at=NOW - timedelta(days=30))
    for truth in (
        _truth(error="timeout"),
        _truth(scheduler_healthy=None),
        _truth(sibling_running=True),
        _truth(state="running", active_tasks=3),
    ):
        verdict = recovery.classify_stuck_run(ancient, truth, now=NOW)
        assert verdict.action == "none", truth


def test_threshold_is_respected_for_stalled_queue():
    recovery = _recovery()
    verdict = recovery.classify_stuck_run(
        _row(started_at=NOW - timedelta(minutes=40)),
        _truth(queued_at=NOW - timedelta(minutes=40)),
        now=NOW,
        threshold=timedelta(minutes=60),
    )
    assert verdict.classification == "too_recent"


def test_cas_sql_is_scoped_fenced_monotonic_and_never_deletes():
    recovery = _recovery()
    status_transitions = importlib.import_module("app.domains.pipeline.status_transitions")
    sql = recovery.recover_update_sql()
    normalized = " ".join(sql.split())
    assert normalized.startswith("UPDATE pipeline_runs SET")
    assert "DELETE" not in sql.upper()
    assert "INSERT" not in sql.upper()
    assert "WHERE run_id = $1" in normalized
    assert "AND tenant_id = $2::uuid" in normalized
    assert "AND workspace_id = $3::uuid" in normalized
    assert "AND LOWER(COALESCE(status, 'unknown')) = $4" in normalized
    assert "AND started_at IS NOT DISTINCT FROM $5::timestamptz" in normalized
    assert "AND fencing_token = $6" in normalized
    assert "fencing_token = fencing_token + 1" in normalized
    assert "lease_expires_at = NULL" in normalized
    assert "finished_at = COALESCE(finished_at, NOW())" in normalized
    assert "error_message = $9::text" in normalized
    monotonic = status_transitions.postgres_monotonic_status("status", "'failed'::text")
    assert f"SET status = {monotonic}" in sql
    assert "status = EXCLUDED.status" not in sql
    assert re.search(r"status\s*=\s*'failed'", sql) is None
    assert "'reconciliation'" in sql and "jsonb_build_array" in sql
    assert "'previous_error', error_message" in sql
    assert "RETURNING run_id, status, fencing_token, finished_at" in normalized


def test_recovery_message_is_the_public_business_copy():
    assert _recovery().RECOVERY_MESSAGE == "Recuperado por el sistema: tiempo de espera agotado"


def test_plan_digest_is_order_independent_and_covers_only_actions():
    recovery = _recovery()
    a = {"run_id": "a", "status": "queued", "started_at": "2026-09-26T10:00:00+00:00", "fencing_token": 1, "classification": "missing_in_airflow", "action": "mark_failed"}
    b = {"run_id": "b", "status": "running", "started_at": None, "fencing_token": 0, "classification": "airflow_terminal", "action": "sync_terminal"}
    idle = {"run_id": "c", "status": "queued", "started_at": None, "fencing_token": 0, "classification": "live", "action": "none"}
    digest = recovery.plan_digest([a, b, idle])
    assert re.fullmatch(r"[a-f0-9]{64}", digest)
    assert digest == recovery.plan_digest([b, idle, a])
    assert digest == recovery.plan_digest([a, b])
    assert digest != recovery.plan_digest([{**a, "fencing_token": 2}, b])
    assert digest != recovery.plan_digest([{**a, "classification": "stalled_queued_paused_dag"}, b])
    assert digest != recovery.plan_digest([{**a, "status": "running"}, b])
    assert digest != recovery.plan_digest([{**a, "started_at": None}, b])


class _Ctx:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *_exc):
        return False


class FakeConn:
    def __init__(self, journal, *, candidates=(), cas_result="auto", dag_rows=(), orphan_rows=()):
        self.journal = journal
        self.candidates = list(candidates)
        self.cas_result = cas_result
        self.dag_rows = list(dag_rows)
        self.orphan_rows = list(orphan_rows)
        self.executed = []
        self.fetches = []

    def transaction(self):
        return _Ctx(self)

    async def execute(self, sql, *args):
        self.executed.append((sql, args))
        if "set_config" in sql:
            self.journal.append(("scope", args))
        return "OK"

    async def fetch(self, sql, *args):
        self.fetches.append((sql, args))
        if "SELECT DISTINCT dag_id" in sql:
            return [dict(row) for row in self.dag_rows]
        if "airflow_dag_run_id = ANY($4::text[])" in sql:
            return [dict(row) for row in self.orphan_rows]
        if "FROM pipeline_runs" in sql:
            return [dict(row) for row in self.candidates]
        return []

    async def fetchrow(self, sql, *args):
        self.fetches.append((sql, args))
        if sql.lstrip().startswith("UPDATE pipeline_runs"):
            self.journal.append(("cas", args[0]))
            if self.cas_result == "auto":
                return {"run_id": args[0], "status": "failed", "fencing_token": args[5] + 1, "finished_at": NOW}
            return self.cas_result
        return None


class FakePool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return _Ctx(self.conn)


def _invoke_factory(journal, *, describe=None, statuses=None, tasks=None, mark=None, raise_on=()):
    calls = []

    async def invoke(server, tool, args, *, user=None):
        calls.append((server, tool, dict(args)))
        assert server == "infra"
        if tool in raise_on:
            raise HTTPException(503, "airflow down")
        if tool == "airflow_describe_dag":
            return (describe or {}).get(args["dag_id"], {"found": True, "is_paused": False, "schedule_kind": "manual", "scheduler_healthy": True, "runs": [], "foreign": {"queued": 0, "running": 0, "stale_queued": 0}})
        if tool == "airflow_get_run_status":
            return (statuses or {}).get(args["dag_run_id"], {"found": False, "state": "not_found"})
        if tool == "airflow_list_task_instances":
            return (tasks or {}).get(args["dag_run_id"], {"found": True, "tasks": []})
        if tool == "airflow_mark_dag_run_failed":
            journal.append(("neutralize", args["dag_run_id"]))
            return (mark or {}).get(args["dag_run_id"], {"marked": True, "found": True, "state": "failed"})
        raise AssertionError(tool)

    invoke.calls = calls
    return invoke


def _user(**overrides):
    user = {"id": 7, "email": "ops@example.com", "role": "admin", "active_tenant_id": TENANT, "active_workspace_id": WORKSPACE}
    user.update(overrides)
    return user


def _run(coro):
    return asyncio.run(coro)


def _recover(conn, invoke, *, record=None, refresh=None, **kwargs):
    service = _service()
    events = []

    async def record_event(**payload):
        events.append(payload)
        conn.journal.append(("audit", payload["resource_id"]))

    async def refresh_status(row, user=None):
        return {**row, "status": "success"}

    async def get_pool():
        return FakePool(conn)

    report = _run(
        service.recover_stuck_runs(
            kwargs.pop("user", _user()),
            mode=kwargs.pop("mode", "apply"),
            actor="user:7",
            invoke=invoke,
            get_db_pool=get_pool,
            refresh_dag_run_status=refresh or refresh_status,
            record_event=record or record_event,
            now=NOW,
            **kwargs,
        )
    )
    return report, events


def test_dry_run_reads_only_and_reports_a_digest():
    journal = []
    conn = FakeConn(journal, candidates=[_row()])
    invoke = _invoke_factory(journal, describe={"sap_successfactors_extract": {"found": True, "is_paused": True, "schedule_kind": "manual", "scheduler_healthy": True, "runs": [], "foreign": {}}}, statuses={"manual__run-1": {"found": True, "state": "queued"}})
    report, events = _recover(conn, invoke, mode="dry_run")
    assert report.mode == "dry_run"
    assert report.counts["candidates"] == 1
    assert report.counts["recoverable"] == 1
    assert report.counts["recovered"] == 0
    assert re.fullmatch(r"[a-f0-9]{64}", report.plan_digest)
    assert report.runs[0]["classification"] == "stalled_queued_paused_dag"
    assert report.runs[0]["action"] == "mark_failed"
    assert report.runs[0]["status_after"] is None
    assert not any(tool == "airflow_mark_dag_run_failed" for _s, tool, _a in invoke.calls)
    assert not [entry for entry in journal if entry[0] in {"cas", "audit", "neutralize"}]
    assert events == []


def test_candidate_query_is_scoped_before_reading():
    journal = []
    conn = FakeConn(journal, candidates=[])
    invoke = _invoke_factory(journal)
    _recover(conn, invoke, mode="dry_run", cartridge="sap_successfactors", dag_ids=["sap_successfactors_extract"], exclude_run_ids=["skip-me"])
    assert journal[0] == ("scope", (TENANT, WORKSPACE))
    sql, args = conn.fetches[0]
    assert "tenant_id = $1::uuid" in sql and "workspace_id = $2::uuid" in sql
    assert "cartridge_id = $4" in sql and "dag_id = ANY($5::text[])" in sql
    assert "NOT (run_id = ANY($6::text[]))" in sql
    assert args[:2] == (TENANT, WORKSPACE)
    assert "DELETE" not in sql.upper()
    assert invoke.calls == []


def test_apply_neutralizes_airflow_before_the_cas_and_audits_in_the_same_transaction():
    journal = []
    conn = FakeConn(journal, candidates=[_row()])
    invoke = _invoke_factory(journal, describe={"sap_successfactors_extract": {"found": True, "is_paused": True, "schedule_kind": "manual", "scheduler_healthy": True, "runs": [], "foreign": {}}}, statuses={"manual__run-1": {"found": True, "state": "queued"}})
    report, events = _recover(conn, invoke)
    order = [entry[0] for entry in journal if entry[0] in {"neutralize", "cas", "audit"}]
    assert order == ["neutralize", "cas", "audit"]
    assert report.counts["recovered"] == 1
    assert report.counts["airflow_neutralized"] == 1
    assert report.runs[0]["status_after"] == "failed"
    mark_args = next(args for _s, tool, args in invoke.calls if tool == "airflow_mark_dag_run_failed")
    assert mark_args == {"dag_id": "sap_successfactors_extract", "dag_run_id": "manual__run-1", "expected_states": ["queued"]}
    cas_sql, cas_args = next((sql, args) for sql, args in conn.fetches if sql.lstrip().startswith("UPDATE"))
    assert cas_args[:6] == ("manual__run-1", TENANT, WORKSPACE, "queued", _row()["started_at"], 3)
    assert json.loads(cas_args[6])["reason"] == "stalled_queued_paused_dag"
    assert json.loads(cas_args[7])["dag_paused"] is True
    assert cas_args[8] == _recovery().RECOVERY_MESSAGE
    event = events[0]
    assert event["connection"] is conn
    assert event["critical"] is True
    assert event["action"] == "pipeline_run.recover_stuck"
    assert event["resource_type"] == "pipeline_run"
    assert event["metadata"]["severity"] == "critical"
    assert event["metadata"]["tenant_id"] == TENANT
    assert event["metadata"]["workspace_id"] == WORKSPACE
    assert event["metadata"]["plan_digest"] == report.plan_digest


def test_failed_neutralization_leaves_the_console_row_untouched():
    journal = []
    conn = FakeConn(journal, candidates=[_row()])
    invoke = _invoke_factory(journal, describe={"sap_successfactors_extract": {"found": True, "is_paused": True, "schedule_kind": "manual", "scheduler_healthy": True, "runs": [], "foreign": {}}}, statuses={"manual__run-1": {"found": True, "state": "queued"}}, mark={"manual__run-1": {"marked": False, "found": True, "reason": "patch_failed"}})
    report, events = _recover(conn, invoke)
    assert report.counts["airflow_neutralize_failed"] == 1
    assert report.counts["recovered"] == 0
    assert not [entry for entry in journal if entry[0] == "cas"]
    assert events == []


def test_airflow_state_change_during_neutralization_is_a_conflict():
    journal = []
    conn = FakeConn(journal, candidates=[_row()])
    invoke = _invoke_factory(journal, describe={"sap_successfactors_extract": {"found": True, "is_paused": True, "schedule_kind": "manual", "scheduler_healthy": True, "runs": [], "foreign": {}}}, statuses={"manual__run-1": {"found": True, "state": "queued"}}, mark={"manual__run-1": {"marked": False, "found": True, "state": "running", "reason": "state_changed"}})
    report, _events = _recover(conn, invoke)
    assert report.counts["conflicts"] == 1
    assert not [entry for entry in journal if entry[0] == "cas"]


def test_lost_cas_writes_no_audit_and_counts_a_conflict():
    journal = []
    conn = FakeConn(journal, candidates=[_row()], cas_result=None)
    invoke = _invoke_factory(journal, statuses={"manual__run-1": {"found": False, "state": "not_found"}})
    report, events = _recover(conn, invoke)
    assert report.counts["conflicts"] == 1
    assert report.counts["recovered"] == 0
    assert events == []
    assert not any(tool == "airflow_mark_dag_run_failed" for _s, tool, _a in invoke.calls)


def test_stale_digest_writes_nothing():
    service = _service()
    journal = []
    conn = FakeConn(journal, candidates=[_row()])
    invoke = _invoke_factory(journal, statuses={"manual__run-1": {"found": False, "state": "not_found"}})
    with pytest.raises(service.PlanChanged) as exc:
        _recover(conn, invoke, expected_plan_digest="0" * 64)
    assert re.fullmatch(r"[a-f0-9]{64}", exc.value.plan_digest)
    assert not [entry for entry in journal if entry[0] in {"cas", "audit", "neutralize"}]


def test_matching_digest_applies_the_reviewed_plan():
    journal = []
    conn = FakeConn(journal, candidates=[_row()])
    invoke = _invoke_factory(journal, statuses={"manual__run-1": {"found": False, "state": "not_found"}})
    dry, _ = _recover(conn, invoke, mode="dry_run")
    applied, events = _recover(conn, invoke, expected_plan_digest=dry.plan_digest)
    assert applied.mode == "applied"
    assert applied.counts["recovered"] == 1
    assert applied.runs[0]["classification"] == "missing_in_airflow"
    assert len(events) == 1


def test_stalled_runs_need_neutralization_permission():
    journal = []
    conn = FakeConn(journal, candidates=[_row()])
    invoke = _invoke_factory(journal, describe={"sap_successfactors_extract": {"found": True, "is_paused": True, "schedule_kind": "manual", "scheduler_healthy": True, "runs": [], "foreign": {}}}, statuses={"manual__run-1": {"found": True, "state": "queued"}})
    report, events = _recover(conn, invoke, neutralize_airflow=False)
    assert report.runs[0]["classification"] == "stalled_queued_paused_dag"
    assert report.runs[0]["action"] == "none"
    assert report.counts["recoverable"] == 0
    assert events == []
    assert not [entry for entry in journal if entry[0] in {"cas", "neutralize"}]


def test_allowed_classes_limit_the_plan():
    journal = []
    conn = FakeConn(journal, candidates=[_row()])
    invoke = _invoke_factory(journal, statuses={"manual__run-1": {"found": False, "state": "not_found"}})
    report, events = _recover(conn, invoke, allowed_classes={"airflow_terminal"})
    assert report.runs[0]["classification"] == "missing_in_airflow"
    assert report.runs[0]["action"] == "none"
    assert events == []


def test_terminal_sync_uses_the_existing_monotonic_refresh():
    journal = []
    conn = FakeConn(journal, candidates=[_row(status="running")])
    invoke = _invoke_factory(journal, statuses={"manual__run-1": {"found": True, "state": "success"}})
    refreshed = []

    async def refresh(row, user=None):
        refreshed.append(row["run_id"])
        return {**row, "status": "success"}

    report, events = _recover(conn, invoke, refresh=refresh)
    assert refreshed == ["manual__run-1"]
    assert report.counts["synced_terminal"] == 1
    assert report.runs[0]["status_after"] == "success"
    assert events == []
    assert not [entry for entry in journal if entry[0] == "cas"]


def test_unreachable_airflow_changes_nothing():
    journal = []
    conn = FakeConn(journal, candidates=[_row(), _row(run_id="manual__run-2", airflow_dag_run_id="manual__run-2")])
    invoke = _invoke_factory(journal, raise_on={"airflow_get_run_status", "airflow_describe_dag"})
    report, events = _recover(conn, invoke)
    assert report.counts["unverifiable"] == 2
    assert report.counts["recovered"] == 0
    assert events == []
    assert "no se pudieron verificar" in report.message_es


def test_recovery_requires_an_active_tenant_and_workspace():
    journal = []
    conn = FakeConn(journal, candidates=[_row()])
    invoke = _invoke_factory(journal)
    with pytest.raises(HTTPException) as exc:
        _recover(conn, invoke, user={"id": 1, "role": "admin"})
    assert exc.value.status_code == 403
    assert conn.fetches == []


def test_candidates_are_capped_at_two_hundred_oldest_first():
    journal = []
    rows = [_row(run_id=f"r{i:03d}", airflow_dag_run_id=f"r{i:03d}", status="success") for i in range(201)]
    conn = FakeConn(journal, candidates=rows)
    invoke = _invoke_factory(journal)
    report, _events = _recover(conn, invoke, mode="dry_run")
    assert report.truncated is True
    assert report.counts["candidates"] == 200
    sql, args = conn.fetches[0]
    assert "ORDER BY started_at ASC NULLS FIRST" in sql
    assert args[-1] == 201


def test_observation_describes_each_dag_once_and_reads_task_activity():
    service = _service()
    journal = []
    rows = [
        _row(run_id="a", airflow_dag_run_id="a", status="running"),
        _row(run_id="b", airflow_dag_run_id="b"),
    ]
    invoke = _invoke_factory(
        journal,
        describe={"sap_successfactors_extract": {"found": True, "is_paused": False, "schedule_kind": "manual", "scheduler_healthy": True, "runs": [{"dag_run_id": "a", "state": "running"}, {"dag_run_id": "b", "state": "queued", "queued_at": "2026-09-26T11:00:00+00:00"}], "foreign": {"queued": 0, "running": 0, "stale_queued": 0}}},
        statuses={"a": {"found": True, "state": "running"}, "b": {"found": True, "state": "queued"}},
        tasks={"a": {"found": True, "tasks": [{"task_id": "extract", "state": "success", "start_date": "2026-09-26T11:10:00+00:00", "end_date": "2026-09-26T11:20:00+00:00"}]}},
    )
    truths = _run(service.observe_airflow_truth(rows, invoke=invoke, user=_user()))
    assert [tool for _s, tool, _a in invoke.calls].count("airflow_describe_dag") == 1
    assert truths["a"].active_tasks == 0
    assert truths["a"].last_task_activity_at == datetime(2026, 9, 26, 11, 20, tzinfo=timezone.utc)
    assert truths["b"].sibling_running is True
    assert truths["b"].queued_at == datetime(2026, 9, 26, 11, 0, tzinfo=timezone.utc)
    assert truths["a"].sibling_running is False


def test_pre_trigger_recovery_fails_open_on_timeout():
    service = _service()
    journal = []
    conn = FakeConn(journal, candidates=[_row()])

    async def slow_invoke(*_args, **_kwargs):
        await asyncio.sleep(5)

    async def get_pool():
        return FakePool(conn)

    async def refresh(row, user=None):
        return row

    started = asyncio.get_event_loop_policy().new_event_loop()
    try:
        result = started.run_until_complete(
            service.recover_before_reservation(
                _user(),
                cartridge="sap_successfactors",
                dag_ids=["sap_successfactors_extract"],
                invoke=slow_invoke,
                get_db_pool=get_pool,
                refresh_dag_run_status=refresh,
                timeout=0.5,
            )
        )
    finally:
        started.close()
    assert result is None
    assert not [entry for entry in journal if entry[0] == "cas"]


def test_pre_trigger_recovery_only_neutralizes_when_enabled():
    service = _service()
    assert service.pre_trigger_allowed_classes(False) == frozenset({"airflow_terminal", "missing_in_airflow"})
    assert {"stalled_queued_paused_dag", "stalled_queued_no_progress", "stalled_running_no_tasks"} <= service.pre_trigger_allowed_classes(True)


def test_pre_trigger_recovery_swallows_scope_errors():
    service = _service()

    async def boom():
        raise AssertionError("pool must not be requested without scope")

    result = _run(
        service.recover_before_reservation(
            {"id": 1, "role": "admin"},
            cartridge="sap_successfactors",
            dag_ids=["sap_successfactors_extract"],
            invoke=_invoke_factory([]),
            get_db_pool=boom,
            refresh_dag_run_status=None,
        )
    )
    assert result is None


def test_running_run_in_a_paused_dag_is_stalled_even_without_scheduler_health():
    recovery = _recovery()
    prod_now = datetime(2026, 9, 26, 18, 0, tzinfo=timezone.utc)
    row = _row(
        run_id="manual__2026-09-26T07:57:00+00:00",
        airflow_dag_run_id="manual__2026-09-26T07:57:00+00:00",
        dag_id="sap_successfactors_extract_all",
        entity="__extract_all__",
        status="running",
        started_at=datetime(2026, 9, 26, 7, 57, tzinfo=timezone.utc),
    )
    for healthy in (True, False, None):
        truth = _truth(
            state="running",
            queued_at=datetime(2026, 9, 26, 7, 57, tzinfo=timezone.utc),
            start_date=datetime(2026, 9, 26, 8, 17, tzinfo=timezone.utc),
            active_tasks=0,
            last_task_activity_at=None,
            dag_paused=True,
            scheduler_healthy=healthy,
        )
        verdict = recovery.classify_stuck_run(row, truth, now=prod_now)
        assert verdict.classification == "stalled_running_no_tasks", healthy
        assert verdict.action == "mark_failed"
        assert verdict.neutralize_airflow is True


def test_prod_shape_task_instances_without_state_are_not_active():
    service = _service()
    journal = []
    run_id = "manual__2026-09-26T07:57:00+00:00"
    row = _row(run_id=run_id, airflow_dag_run_id=run_id, dag_id="sap_successfactors_extract_all", entity="__extract_all__", status="running")
    invoke = _invoke_factory(
        journal,
        describe={"sap_successfactors_extract_all": {"found": True, "is_paused": True, "schedule_kind": "manual", "scheduler_healthy": True, "runs": [{"dag_run_id": run_id, "state": "running", "queued_at": "2026-09-26T07:57:00+00:00", "conf": {}}], "running_truncated": False, "foreign": {"queued": 0, "running": 0, "stale_queued": 0, "stale_running": 0}}},
        statuses={run_id: {"found": True, "state": "running", "start_date": "2026-09-26T08:17:00+00:00"}},
        tasks={run_id: {"found": True, "tasks": [{"task_id": t, "state": None, "start_date": None, "end_date": None} for t in ("plan", "extract", "summarize")]}},
    )
    truths = _run(service.observe_airflow_truth([row], invoke=invoke, user=_user()))
    truth = truths[run_id]
    assert truth.active_tasks == 0
    assert truth.last_task_activity_at is None
    assert truth.dag_paused is True
    verdict = _recovery().classify_stuck_run(row, truth, now=datetime(2026, 9, 26, 18, 0, tzinfo=timezone.utc))
    assert verdict.classification == "stalled_running_no_tasks"


def test_truncated_running_page_without_a_visible_sibling_is_unverifiable():
    service = _service()
    journal = []
    row = _row()
    base = {"found": True, "is_paused": False, "schedule_kind": "manual", "scheduler_healthy": True, "runs": [], "foreign": {"queued": 0, "running": 0, "stale_queued": 0, "stale_running": 0}}
    invoke = _invoke_factory(journal, describe={"sap_successfactors_extract": {**base, "running_truncated": True}}, statuses={"manual__run-1": {"found": True, "state": "queued"}})
    truth = _run(service.observe_airflow_truth([row], invoke=invoke, user=_user()))["manual__run-1"]
    assert truth.sibling_running is None
    assert _recovery().classify_stuck_run(row, truth, now=NOW).classification == "unverifiable"

    invoke = _invoke_factory(journal, describe={"sap_successfactors_extract": {**base, "running_truncated": True, "foreign": {**base["foreign"], "running": 1}}}, statuses={"manual__run-1": {"found": True, "state": "queued"}})
    truth = _run(service.observe_airflow_truth([row], invoke=invoke, user=_user()))["manual__run-1"]
    assert truth.sibling_running is True
    assert _recovery().classify_stuck_run(row, truth, now=NOW).classification == "waiting_turn"

    invoke = _invoke_factory(journal, describe={"sap_successfactors_extract": {**base, "running_truncated": False}}, statuses={"manual__run-1": {"found": True, "state": "queued"}})
    truth = _run(service.observe_airflow_truth([row], invoke=invoke, user=_user()))["manual__run-1"]
    assert truth.sibling_running is False
    assert _recovery().classify_stuck_run(row, truth, now=NOW).classification == "stalled_queued_no_progress"


def test_candidates_are_limited_to_the_callers_visible_cartridges():
    journal = []
    conn = FakeConn(journal, candidates=[])
    invoke = _invoke_factory(journal)
    _recover(conn, invoke, mode="dry_run", visible_cartridges=["sap_successfactors"])
    sql, args = conn.fetches[0]
    assert "cartridge_id = ANY($4::text[])" in sql
    assert args[3] == ["sap_successfactors"]
    conn = FakeConn(journal, candidates=[])
    _recover(conn, invoke, mode="dry_run", visible_cartridges=[])
    sql, args = conn.fetches[0]
    assert args[3] == []
    conn = FakeConn(journal, candidates=[])
    _recover(conn, invoke, mode="dry_run")
    assert "cartridge_id = ANY" not in conn.fetches[0][0]


def _orphan_describe(*, paused=True, age=timedelta(hours=10), state="queued", run_id="manual__orphan"):
    return {
        "found": True,
        "is_paused": paused,
        "schedule_kind": "manual",
        "scheduler_healthy": True,
        "runs": [{"dag_run_id": run_id, "state": state, "queued_at": (NOW - age).isoformat(), "conf": {"tenant_id": TENANT, "workspace_id": WORKSPACE}}],
        "running_truncated": False,
        "foreign": {"queued": 0, "running": 0, "stale_queued": 0, "stale_running": 0},
    }


def _orphan_row(status="failed", **overrides):
    row = _row(
        run_id="manual__orphan",
        airflow_dag_run_id="manual__orphan",
        dag_id="sap_successfactors_extract_all",
        entity="__extract_all__",
        status=status,
        fencing_token=4,
    )
    row.update(overrides)
    return row


def test_orphan_run_left_pending_in_a_paused_dag_is_planned_then_neutralized_with_audit():
    journal = []
    conn = FakeConn(journal, candidates=[], dag_rows=[{"dag_id": "sap_successfactors_extract_all"}, {"dag_id": "dataset_refresh_chain"}], orphan_rows=[_orphan_row()])
    invoke = _invoke_factory(journal, describe={"sap_successfactors_extract_all": _orphan_describe()})
    dry, events = _recover(conn, invoke, mode="dry_run", orphan_scan=True)
    assert [run["classification"] for run in dry.runs] == ["airflow_orphan"]
    assert dry.runs[0]["action"] == "neutralize_airflow"
    assert dry.runs[0]["status_before"] == "failed"
    assert dry.counts["recoverable"] == 1
    assert events == []
    assert not [entry for entry in journal if entry[0] == "neutralize"]
    described = [args["dag_id"] for _s, tool, args in invoke.calls if tool == "airflow_describe_dag"]
    assert described == ["sap_successfactors_extract_all"]

    applied, events = _recover(conn, invoke, orphan_scan=True, expected_plan_digest=dry.plan_digest)
    assert [entry for entry in journal if entry[0] in {"neutralize", "cas"}] == [("neutralize", "manual__orphan")]
    assert applied.counts["airflow_neutralized"] == 1
    assert applied.counts["recovered"] == 0
    assert "Se detuvieron en Airflow 1" in applied.message_es
    mark_args = next(args for _s, tool, args in invoke.calls if tool == "airflow_mark_dag_run_failed")
    assert mark_args["expected_states"] == ["queued"]
    assert events[0]["action"] == "pipeline_run.recover_stuck"
    assert events[0]["critical"] is True
    assert events[0]["metadata"]["classification"] == "airflow_orphan"
    assert events[0]["metadata"]["console_status"] == "failed"


@pytest.mark.parametrize(
    "describe,rows,exclude",
    [
        (_orphan_describe(paused=False), [_orphan_row()], ()),
        (_orphan_describe(age=timedelta(minutes=3)), [_orphan_row()], ()),
        (_orphan_describe(), [_orphan_row(), _orphan_row(run_id="manual__orphan:User", status="queued")], ()),
        (_orphan_describe(), [], ()),
        (_orphan_describe(), [_orphan_row()], ("manual__orphan",)),
        (_orphan_describe(state="success"), [_orphan_row()], ()),
    ],
    ids=["unpaused_dag", "recent_run", "open_console_row", "no_console_row", "excluded", "not_pending"],
)
def test_orphan_scan_only_touches_closed_runs_stuck_in_a_paused_dag(describe, rows, exclude):
    journal = []
    conn = FakeConn(journal, candidates=[], dag_rows=[{"dag_id": "sap_successfactors_extract_all"}], orphan_rows=rows)
    invoke = _invoke_factory(journal, describe={"sap_successfactors_extract_all": describe})
    report, events = _recover(conn, invoke, orphan_scan=True, exclude_run_ids=exclude)
    assert report.runs == []
    assert events == []
    assert not [entry for entry in journal if entry[0] == "neutralize"]


def test_orphan_lookup_is_scoped_and_respects_visible_cartridges():
    journal = []
    conn = FakeConn(journal, candidates=[], dag_rows=[{"dag_id": "sap_successfactors_extract_all"}], orphan_rows=[])
    invoke = _invoke_factory(journal, describe={"sap_successfactors_extract_all": _orphan_describe()})
    _recover(conn, invoke, mode="dry_run", orphan_scan=True, visible_cartridges=["sap_successfactors"], cartridge="sap_successfactors")
    dag_sql, dag_args = next((sql, args) for sql, args in conn.fetches if "SELECT DISTINCT dag_id" in sql)
    assert "tenant_id = $1::uuid" in dag_sql and "workspace_id = $2::uuid" in dag_sql
    assert dag_args[:4] == (TENANT, WORKSPACE, "sap_successfactors", ["sap_successfactors"])
    lookup_sql, lookup_args = next((sql, args) for sql, args in conn.fetches if "airflow_dag_run_id = ANY($4::text[])" in sql)
    assert "tenant_id = $1::uuid" in lookup_sql and "cartridge_id = ANY($5::text[])" in lookup_sql
    assert lookup_args == (TENANT, WORKSPACE, ["sap_successfactors_extract_all"], ["manual__orphan"], ["sap_successfactors"])
    assert ("scope", (TENANT, WORKSPACE)) in journal


def test_orphans_need_neutralization_permission_and_the_orphan_class():
    journal = []
    conn = FakeConn(journal, candidates=[], dag_rows=[{"dag_id": "sap_successfactors_extract_all"}], orphan_rows=[_orphan_row()])
    invoke = _invoke_factory(journal, describe={"sap_successfactors_extract_all": _orphan_describe()})
    report, events = _recover(conn, invoke, orphan_scan=True, neutralize_airflow=False)
    assert report.runs[0]["action"] == "none"
    report, events = _recover(conn, invoke, orphan_scan=True, allowed_classes={"missing_in_airflow"})
    assert report.runs[0]["action"] == "none"
    assert events == []
    assert not [entry for entry in journal if entry[0] == "neutralize"]
