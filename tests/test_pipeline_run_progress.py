from __future__ import annotations

import importlib
import json
from datetime import datetime, timedelta, timezone

import pytest


NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)


def _progress():
    return importlib.import_module("app.domains.pipeline.run_progress")


def _row(**overrides):
    row = {
        "run_id": "manual__1",
        "airflow_dag_run_id": "manual__1",
        "dag_id": "sap_successfactors_extract",
        "entity": "User",
        "status": "queued",
        "started_at": NOW - timedelta(minutes=1),
        "finished_at": None,
        "record_count": None,
        "error_message": None,
        "extra": {},
    }
    row.update(overrides)
    return row


def _aggregate(**overrides):
    return _row(
        run_id="manual__all",
        airflow_dag_run_id="manual__all",
        dag_id="sap_successfactors_extract_all",
        entity="__extract_all__",
        **overrides,
    )


def _child(entity, status="success", record_count=None, **extra):
    return _row(
        run_id=f"manual__all:{entity}",
        airflow_dag_run_id="manual__all",
        entity=entity,
        status=status,
        record_count=record_count,
        extra=extra,
    )


def _build(row, children=(), **kwargs):
    return _progress().build_run_progress(row, children, now=NOW, **kwargs)


def test_queued_run_is_connecting_with_unknown_counts():
    result = _build(_row())
    assert result["phase"] == "connecting"
    assert result["phase_index"] == 1
    assert result["terminal"] is False
    assert result["outcome"] is None
    assert result["record_count"] is None
    assert result["entities_done"] is None
    assert result["stalled"] is False
    assert result["error"] is None


def test_queued_past_threshold_is_stalled_but_not_failed():
    result = _build(_row(started_at=NOW - timedelta(minutes=16)))
    assert result["stalled"] is True
    assert result["phase"] == "connecting"
    assert result["outcome"] is None


def test_running_run_is_extracting_without_inventing_counts():
    result = _build(_row(status="running"))
    assert (result["phase"], result["phase_index"]) == ("extracting", 2)
    assert result["record_count"] is None
    assert result["stalled"] is False


def test_downstream_refresh_in_flight_is_building():
    running = _build(_row(status="running", extra={"silver_refresh": {"status": "running"}}))
    assert running["phase"] == "building"
    finished = _build(_row(status="success", record_count=12, extra={"gold_refresh": {"status": "queued"}}))
    assert finished["phase"] == "building"
    assert finished["terminal"] is True


def test_success_with_nothing_pending_is_ready_with_real_count():
    result = _build(_row(status="success", record_count=120, finished_at=NOW, extra={"silver_refresh": {"status": "success"}}))
    assert (result["phase"], result["phase_index"]) == ("ready", 4)
    assert result["outcome"] == "success"
    assert result["record_count"] == 120


def test_zero_rows_is_reported_only_when_the_run_says_so():
    assert _build(_row(status="success", record_count=0))["record_count"] == 0
    assert _build(_row(status="success"))["record_count"] is None
    assert _build(_row(status="success", record_count=-3))["record_count"] is None


def test_partial_and_blocked_classification_are_warnings():
    partial = _build(_row(status="partial", error_message="2 campos sin metadatos"))
    assert partial["outcome"] == "partial"
    assert partial["phase"] == "ready"
    assert partial["error"] == "2 campos sin metadatos"
    blocked = _build(_row(status="failed", extra={"classification": {"code": "SUCCESSFACTORS_PERMISSION", "error": "sin permiso"}}))
    assert blocked["status"] == "partial"
    assert blocked["outcome"] == "partial"
    assert blocked["error"] == "sin permiso"
    noop = _build(_row(status="noop"))
    assert noop["outcome"] == "success"
    assert noop["phase"] == "ready"


def test_failure_phase_follows_the_evidence():
    never_started = _build(_row(status="failed", error_message="credenciales inválidas"))
    assert never_started["phase"] == "connecting"
    assert never_started["outcome"] == "failed"
    assert never_started["error"] == "credenciales inválidas"
    observed = _build(_row(status="failed", extra={"airflow_observation": {"state": "running"}}))
    assert observed["phase"] == "extracting"
    counted = _build(_row(status="failed", record_count=40))
    assert counted["phase"] == "extracting"


def test_recovered_runs_are_flagged():
    recovery = _progress().build_run_progress
    message = importlib.import_module("app.domains.pipeline.stuck_run_recovery").RECOVERY_MESSAGE
    result = recovery(
        _row(status="failed", error_message=message, extra=json.dumps({"recovery": {"reason": "missing_in_airflow"}})),
        now=NOW,
    )
    assert result["recovered"] is True
    assert result["outcome"] == "failed"
    assert result["error"] == message
    assert _build(_row(status="failed", error_message=message))["recovered"] is True
    assert _build(_row(status="failed", error_message="otro"))["recovered"] is False


def test_aggregate_sums_only_known_finished_children():
    children = [
        _child("User", record_count=10),
        _child("PerPhone", record_count=None),
        _child("PerEmail", status="running", record_count=7),
    ]
    result = _build(_aggregate(status="running"), children)
    assert result["phase"] == "extracting"
    assert result["record_count"] == 10
    assert result["entities_done"] == 2
    assert result["entities_total"] is None


def test_aggregate_without_children_keeps_counts_unknown():
    result = _build(_aggregate(status="running"))
    assert result["record_count"] is None
    assert result["entities_done"] is None
    assert result["phase"] == "extracting"


def test_aggregate_building_needs_evidence():
    finished_so_far = _build(_aggregate(status="running"), [_child("User", record_count=3), _child("PerPhone", status="partial")])
    assert finished_so_far["phase"] == "extracting"
    all_done = _build(_aggregate(status="running", extra={"selected": 2}), [_child("User", record_count=3), _child("PerPhone", status="partial")])
    assert all_done["phase"] == "building"
    refreshing = _build(_aggregate(status="running"), [_child("User", record_count=3, silver_refresh={"status": "running"})])
    assert refreshing["phase"] == "building"
    gold = _build(_aggregate(status="running"), [_child("User", record_count=3), _child("PerEmail", status="running"), _child("gold_foundation", transition="gold_materialized")])
    assert gold["phase"] == "building"
    assert gold["entities_done"] == 1


def test_aggregate_final_row_reports_selected_entities_and_total():
    result = _build(_aggregate(status="success", record_count=99, finished_at=NOW, extra={"selected": 5, "summary": {}}), [_child("User", record_count=90), _child("gold_foundation", transition="gold_materialized", record_count=500)])
    assert result["phase"] == "ready"
    assert result["record_count"] == 99
    assert result["entities_total"] == 5
    assert result["entities_done"] == 1


def test_requested_run_resolution_and_children():
    progress = _progress()
    parent = _aggregate(status="running")
    child = _child("User")
    other = _row(run_id="manual__other", airflow_dag_run_id="manual__other")
    rows = [child, other, parent]
    assert progress.resolve_requested_run("manual__all", rows) is parent
    assert progress.resolve_requested_run("manual__other", rows) is other
    assert progress.resolve_requested_run("manual__missing", rows) is None
    assert progress.children_of(parent, rows) == [child]
    assert progress.children_of(other, rows) == []


@pytest.mark.parametrize(
    "row,expected",
    [
        (_row(extra={}), True),
        (_row(extra={"airflow_observation": {"state": "queued", "observed_at": (NOW - timedelta(seconds=2)).isoformat()}}), False),
        (_row(extra={"airflow_observation": {"state": "queued", "observed_at": (NOW - timedelta(seconds=9)).isoformat()}}), True),
        (_row(status="success"), False),
        (_row(status="failed"), False),
    ],
)
def test_airflow_refresh_is_throttled_to_five_seconds(row, expected):
    assert _progress().needs_airflow_refresh(row, now=NOW) is expected
