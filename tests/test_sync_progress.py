from __future__ import annotations

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
