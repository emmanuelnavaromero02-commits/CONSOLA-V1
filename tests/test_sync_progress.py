from __future__ import annotations

from datetime import datetime, timezone

from app.services import sync_progress


def test_normalize_sync_step_payload_recomputes_terminal_percent_from_counts():
    step = sync_progress.normalize_sync_step_payload(
        {
            "id": "bronze",
            "label": "Bronze",
            "status": "partial",
            "completed": 40,
            "total": 40,
            "percent": 0,
        }
    )

    assert step["percent"] == 100


def test_merge_sync_steps_keeps_order_and_normalizes_update():
    steps = sync_progress.merge_sync_steps(
        None,
        {"bronze": {"status": "partial", "completed": 2, "total": 4, "percent": 0}},
    )

    assert [step["id"] for step in steps] == [
        "connection",
        "bronze",
        "silver_gold",
        "control_room",
        "agents_intelligence",
    ]
    assert steps[1]["status"] == "partial"
    assert steps[1]["percent"] == 50


def test_sync_step_entity_summary_keeps_blocker_reason_and_fields():
    summary = sync_progress.sync_step_entity_summary(
        [
            {
                "entity": "CareerWorksheet",
                "status": "blocked",
                "row_count": 0,
                "extra": {
                    "reason": "entity_not_exposed_in_sap",
                    "fields_missing": ["userId"],
                },
            },
            {"entity": "EmpJob", "status": "success", "row_count": 1288},
            {"entity": "__sync_now__", "status": "running", "row_count": 0},
        ],
        aggregate_entity="__extract_all__",
        sync_now_entity="__sync_now__",
    )

    assert summary["counts"] == {
        "success": 1,
        "partial": 0,
        "blocked": 1,
        "failed": 0,
    }
    assert summary["blockers"][0]["entity"] == "CareerWorksheet"
    assert summary["blockers"][0]["reason"] == "entity_not_exposed_in_sap"
    assert summary["blockers"][0]["fields_missing"] == ["userId"]


def test_public_sync_payload_calculates_average_step_progress():
    payload = sync_progress.public_sync_payload(
        {
            "run_id": "sync_now:sap_successfactors:1",
            "cartridge_id": "sap_successfactors",
            "status": "running",
            "mode": "incremental",
            "started_at": datetime(2026, 7, 3, tzinfo=timezone.utc),
            "finished_at": None,
            "error_message": None,
        },
        {
            "target": "all",
            "steps": [
                {"id": "connection", "status": "success", "completed": 1, "total": 1},
                {"id": "bronze", "status": "partial", "completed": 2, "total": 4},
            ],
            "triggered_entities": [{"entity": "__extract_all__"}],
            "control_room_ready": True,
        },
        terminal_statuses={"success", "failed", "partial"},
    )

    assert payload["active"] is True
    assert payload["progress_percent"] == 75
    assert payload["steps"][0]["percent"] == 100
    assert payload["steps"][1]["percent"] == 50
    assert payload["started_at"] == "2026-07-03T00:00:00+00:00"
    assert payload["triggered_entities"] == [{"entity": "__extract_all__"}]
    assert payload["control_room_ready"] is True


def test_inactive_sync_run_payload_is_explicit():
    payload = sync_progress.inactive_sync_run_payload(
        cartridge="sap_successfactors",
        mode="incremental",
        target="all",
        conn_id="femsa_sf",
    )

    assert payload["status"] == "skipped"
    assert payload["active"] is False
    assert payload["reason"] == "no_active_sync_run"
    assert payload["conn_id"] == "femsa_sf"


def test_extract_all_public_response_separates_blocked_failed_and_partial():
    payload = sync_progress.extract_all_public_response(
        {
            "cartridge": "sap_successfactors",
            "triggered": [{"entity": "EmpJob"}],
            "errors": [
                {"entity": "CareerWorksheet", "status_code": 404},
                {"entity": "User", "status_code": 500},
            ],
            "partial": [{"entity": "JobApplication"}],
            "skipped_explicit": [{"entity": "LearningHistory"}],
        }
    )

    assert payload["attempted"] == 5
    assert payload["count"] == 1
    assert payload["error_count"] == 2
    assert payload["blocked"] == [{"entity": "CareerWorksheet", "status_code": 404}]
    assert payload["failed"] == [{"entity": "User", "status_code": 500}]
    assert payload["summary"] == {
        "attempted": 5,
        "triggered": 1,
        "errors": 2,
        "blocked": 1,
        "failed": 1,
        "partial": 1,
        "skipped_explicit": 1,
    }


def test_child_gold_refresh_summary_counts_partial_results():
    summary = sync_progress.child_gold_refresh_summary(
        [
            {
                "extra": {
                    "gold_refresh": {
                        "status": "partial",
                        "materialized": 2,
                        "total": 3,
                        "results": [
                            {"name": "ok_one", "status": "ok"},
                            {"name": "failed_one", "status": "error"},
                        ],
                    }
                }
            },
            {
                "extra": {
                    "gold_refresh": {
                        "results": [
                            {"name": "ok_two", "status": "ok"},
                        ],
                    }
                }
            },
        ]
    )

    assert summary["status"] == "partial"
    assert summary["materialized"] == 3
    assert summary["total"] == 4
    assert summary["failed"] == 1


def test_gold_refresh_dataset_names_returns_only_successful_names():
    assert sync_progress.gold_refresh_dataset_names(
        {
            "results": [
                {"name": "b_dataset", "status": "ok"},
                {"name": "a_dataset", "status": "OK"},
                {"name": "bad_dataset", "status": "error"},
                {"name": "", "status": "ok"},
            ]
        }
    ) == ["a_dataset", "b_dataset"]


def test_control_room_gold_refresh_terminal_statuses():
    assert sync_progress.control_room_gold_refresh_terminal({"status": "completed"})
    assert sync_progress.control_room_gold_refresh_terminal({"status": "not_ready"})
    assert not sync_progress.control_room_gold_refresh_terminal({"status": "running"})
    assert not sync_progress.control_room_gold_refresh_terminal(None)


def test_sync_entity_idempotency_key_truncates_long_material():
    key = sync_progress.sync_entity_idempotency_key("x" * 150, "Candidate")

    assert key is not None
    assert len(key) <= 160
    assert key.startswith("x" * 100)


def test_sync_now_run_id_from_request_id_is_stable():
    first = sync_progress.sync_now_run_id_from_request_id(
        cartridge="sap_successfactors",
        request_id="manual-1",
        lock_key="tenant:workspace:sap_successfactors",
    )
    second = sync_progress.sync_now_run_id_from_request_id(
        cartridge="sap_successfactors",
        request_id="manual-1",
        lock_key="tenant:workspace:sap_successfactors",
    )

    assert first == second
    assert first.startswith("sync_now:sap_successfactors:")


def test_sync_run_age_seconds_handles_strings_and_future_dates():
    now = datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc)

    assert sync_progress.sync_run_age_seconds(
        {"started_at": "2026-07-03T11:59:00+00:00"},
        now=now,
    ) == 60
    assert sync_progress.sync_run_age_seconds(
        {"started_at": "2026-07-03T12:01:00+00:00"},
        now=now,
    ) == 0
    assert sync_progress.sync_run_age_seconds({"started_at": "not-a-date"}, now=now) is None


def test_sync_run_needs_final_reconcile_for_successfactors_aggregate_summary():
    steps = [
        {"id": "connection", "status": "success"},
        {"id": "bronze", "status": "success"},
    ]
    row = {"status": "partial", "cartridge_id": "sap_successfactors"}
    extra = {
        "steps": steps,
        "triggered_entities": [
            {"entity": "__extract_all__", "dag_run_id": "aggregate-run"}
        ],
        "control_room_checked_at": "2026-07-03T12:00:00Z",
    }

    assert sync_progress.sync_run_needs_final_reconcile(
        row,
        extra,
        terminal_statuses={"success", "partial", "failed"},
        initial_step_count=2,
        aggregate_entity="__extract_all__",
    )
    extra["extract_all_summary_seen"] = True
    assert not sync_progress.sync_run_needs_final_reconcile(
        row,
        extra,
        terminal_statuses={"success", "partial", "failed"},
        initial_step_count=2,
        aggregate_entity="__extract_all__",
    )


def test_sync_run_needs_final_reconcile_waits_for_pending_steps():
    assert sync_progress.sync_run_needs_final_reconcile(
        {"status": "success", "cartridge_id": "replicon"},
        {"steps": [{"id": "bronze", "status": "running"}]},
        terminal_statuses={"success", "partial", "failed"},
        initial_step_count=1,
        aggregate_entity="__extract_all__",
    )


def test_sync_errors_retryable_only_allows_transient_errors():
    assert sync_progress.sync_errors_retryable(
        [{"status_code": 500}, {"status_code": 400, "error": "timeout from airflow"}]
    )
    assert not sync_progress.sync_errors_retryable(
        [{"status_code": 404, "error": "entity missing"}]
    )
    assert not sync_progress.sync_errors_retryable([])


def test_sync_child_progress_rows_marks_aggregate_pending_as_running():
    progress = sync_progress.sync_child_progress_rows(
        [
            {
                "entity": "__extract_all__",
                "status": "success",
                "extra": {},
            }
        ],
        aggregate_entity="__extract_all__",
        sync_now_entity="__sync_now__",
    )

    assert progress["aggregate_summary_pending"] is True
    assert progress["aggregate_payload_ready"] is False
    assert progress["entity_child_rows"] == []
    assert progress["progress_rows"][0]["status"] == "running"


def test_sync_child_progress_rows_prefers_entity_rows_when_aggregate_has_payload():
    rows = [
        {
            "entity": "__extract_all__",
            "status": "success",
            "extra": {"gold_refresh": {"status": "success"}},
        },
        {"entity": "EmpJob", "status": "success"},
        {"entity": "__sync_now__", "status": "running"},
    ]

    progress = sync_progress.sync_child_progress_rows(
        rows,
        aggregate_entity="__extract_all__",
        sync_now_entity="__sync_now__",
    )

    assert progress["aggregate_summary_pending"] is False
    assert progress["aggregate_payload_ready"] is True
    assert progress["progress_rows"] == [{"entity": "EmpJob", "status": "success"}]


def test_sync_child_status_counts_normalizes_pipeline_child_states():
    counts = sync_progress.sync_child_status_counts(
        [
            {"status": "success"},
            {"status": "partial"},
            {"status": "blocked"},
            {"status": "error"},
            {"status": "queued"},
        ],
        terminal_statuses={"success", "partial", "blocked", "failed", "error"},
    )

    assert counts == {
        "statuses": ["success", "partial", "blocked", "error", "queued"],
        "running": True,
        "failed": 1,
        "success": 1,
        "partial": 1,
        "blocked": 1,
        "terminal": 4,
    }


def test_sync_pipeline_materialization_summary_prefers_gold_refresh_counts():
    summary = sync_progress.sync_pipeline_materialization_summary(
        [
            {
                "bronze": {"status": "fresh"},
                "silver": [{"status": "fresh"}, {"status": "missing"}],
                "gold": [{"status": "fresh"}, {"status": "fresh"}, {"status": "missing"}],
            },
            {"bronze": {"status": "missing"}, "silver": [], "gold": []},
            "not-a-row",
        ],
        {"status": "partial", "materialized": 4, "total": 5},
    )

    assert summary["bronze_ready"] == 1
    assert summary["silver_ready"] == 1
    assert summary["gold_ready"] == 4
    assert summary["gold_total"] == 5
    assert summary["gold_partial"] is True


def test_sync_connection_step_update_reflects_started_pipeline():
    pending = sync_progress.sync_connection_step_update(
        triggered=[],
        child_rows=[],
        bronze_ready=0,
    )
    started = sync_progress.sync_connection_step_update(
        triggered=[{"entity": "EmpJob"}],
        child_rows=[],
        bronze_ready=0,
    )

    assert pending["status"] == "running"
    assert pending["percent"] == 20
    assert started["status"] == "success"
    assert started["percent"] == 100


def test_sync_bronze_step_update_summarizes_running_partial_and_success():
    summary = {
        "entities": [{"entity": "EmpJob", "status": "success"}],
        "blockers": [{"entity": "CareerInterest", "reason": "entity_not_exposed"}],
    }
    running = sync_progress.sync_bronze_step_update(
        running_children=True,
        aggregate_summary_pending=False,
        progress_count=3,
        failed_children=0,
        success_children=0,
        partial_children=0,
        blocked_children=0,
        errors=[],
        child_done=1,
        child_total=4,
        bronze_ready=0,
        entity_summary=summary,
    )
    partial = sync_progress.sync_bronze_step_update(
        running_children=False,
        aggregate_summary_pending=False,
        progress_count=4,
        failed_children=1,
        success_children=2,
        partial_children=1,
        blocked_children=1,
        errors=[{"entity": "User"}],
        child_done=5,
        child_total=6,
        bronze_ready=0,
        entity_summary=summary,
    )
    success = sync_progress.sync_bronze_step_update(
        running_children=False,
        aggregate_summary_pending=False,
        progress_count=0,
        failed_children=0,
        success_children=0,
        partial_children=0,
        blocked_children=0,
        errors=[],
        child_done=0,
        child_total=1,
        bronze_ready=8,
        entity_summary=summary,
    )

    assert running is not None
    assert running["status"] == "running"
    assert running["detail"] == "3 corridas del sync actual en curso."
    assert partial is not None
    assert partial["status"] == "partial"
    assert partial["detail"] == "2 OK; 1 parciales; 1 bloqueadas; 2 con error."
    assert partial["blockers"] == summary["blockers"]
    assert success is not None
    assert success["status"] == "success"
    assert success["completed"] == 8


def test_sync_silver_gold_step_update_distinguishes_waiting_partial_and_failed():
    queued = sync_progress.sync_silver_gold_step_update(
        bronze_status="running",
        running_children=True,
        gold_total=2,
        silver_ready=1,
        gold_ready=0,
        failed_children=0,
        partial_children=0,
        blocked_children=0,
        gold_partial=False,
        errors=[],
    )
    partial = sync_progress.sync_silver_gold_step_update(
        bronze_status="partial",
        running_children=False,
        gold_total=5,
        silver_ready=4,
        gold_ready=3,
        failed_children=0,
        partial_children=1,
        blocked_children=0,
        gold_partial=True,
        errors=[],
    )
    failed = sync_progress.sync_silver_gold_step_update(
        bronze_status="failed",
        running_children=False,
        gold_total=0,
        silver_ready=0,
        gold_ready=0,
        failed_children=1,
        partial_children=0,
        blocked_children=0,
        gold_partial=False,
        errors=[],
    )

    assert queued is not None
    assert queued["status"] == "queued"
    assert partial is not None
    assert partial["status"] == "partial"
    assert partial["completed"] == 3
    assert failed is not None
    assert failed["status"] == "failed"


def test_sync_agents_intelligence_step_update_covers_common_states():
    skipped = sync_progress.sync_agents_intelligence_step_update(
        applies=False,
        can_run_agentops=False,
        running_children=False,
        agentops_refresh={},
    )
    waiting = sync_progress.sync_agents_intelligence_step_update(
        applies=True,
        can_run_agentops=False,
        running_children=True,
        agentops_refresh={},
    )
    success = sync_progress.sync_agents_intelligence_step_update(
        applies=True,
        can_run_agentops=True,
        running_children=False,
        agentops_refresh={"status": "success", "completed": 1, "total": 1},
    )
    failed = sync_progress.sync_agents_intelligence_step_update(
        applies=True,
        can_run_agentops=True,
        running_children=False,
        agentops_refresh={
            "status": "failed",
            "completed": 0,
            "failed": 1,
            "reason": "runner unavailable",
        },
    )

    assert skipped["status"] == "skipped"
    assert skipped["percent"] == 100
    assert waiting["status"] == "queued"
    assert success["status"] == "success"
    assert success["detail"] == "1/1 monitores ejecutados con AgentOps."
    assert failed["status"] == "failed"
    assert failed["detail"] == "runner unavailable"
