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
