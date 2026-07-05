from __future__ import annotations

from datetime import datetime, timezone

from app.services import sync_control_room


def test_gold_refresh_airflow_run_id_prefers_aggregate_child_run():
    assert (
        sync_control_room.gold_refresh_airflow_run_id(
            {"run_id": "parent"},
            [
                {"entity": "EmpJob", "airflow_dag_run_id": "entity-run"},
                {"entity": "__extract_all__", "airflow_dag_run_id": "aggregate-run"},
            ],
            aggregate_entity="__extract_all__",
        )
        == "aggregate-run"
    )
    assert (
        sync_control_room.gold_refresh_airflow_run_id(
            {"run_id": "parent"},
            [],
            aggregate_entity="__extract_all__",
        )
        == "parent"
    )


def test_gold_refresh_control_payloads_keep_public_contracts():
    assert sync_control_room.gold_refresh_skipped_payload("now") == {
        "status": "skipped",
        "checked_at": "now",
        "reason": "No Gold datasets were materialized successfully.",
        "datasets": [],
    }
    assert sync_control_room.gold_refresh_missing_scope_payload(
        checked_at="now",
        datasets=["gold_a"],
    ) == {
        "status": "failed",
        "checked_at": "now",
        "reason": "Missing tenant/workspace scope for Control Room Gold refresh.",
        "datasets": ["gold_a"],
    }


def test_gold_refresh_intelligence_payload_is_aggregate_and_scoped_by_run_ref():
    payload = sync_control_room.gold_refresh_intelligence_payload(
        cartridge="sap_successfactors",
        datasets=["gold_a", "gold_b"],
        row={
            "run_id": "sync-run",
            "finished_at": datetime(2026, 7, 3, tzinfo=timezone.utc),
        },
        airflow_dag_run_id="airflow-run",
        run_ref=sync_control_room.gold_refresh_run_ref(
            workspace_id="workspace-1",
            cartridge="sap_successfactors",
            airflow_dag_run_id="airflow-run",
        ),
        gold_refresh_summary={"status": "partial"},
    )

    assert payload["run_ref"] == "gold-refresh:workspace-1:sap_successfactors:airflow-run"
    assert payload["include_external"] is False
    assert payload["dry_run"] is False
    assert payload["metadata"] == {
        "trigger": "sync_now_gold_refresh",
        "pipeline_run_id": "sync-run",
        "airflow_dag_run_id": "airflow-run",
        "materialization_status": "partial",
        "datasets_received": ["gold_a", "gold_b"],
        "finished_at": "2026-07-03T00:00:00+00:00",
    }


def test_gold_refresh_result_payload_normalizes_engine_output():
    payload = sync_control_room.gold_refresh_result_payload(
        checked_at="now",
        run_ref="fallback-run-ref",
        datasets=["gold_a"],
        result={
            "status": "completed",
            "run_ref": "real-run-ref",
            "intelligence_run_id": 123,
            "signals": [{"id": "one"}],
            "skipped": [{"id": "skip"}],
            "idempotent": True,
            "dataset_unavailable_count": 2,
            "insufficient_history_count": 3,
            "skipped_counts": {"missing": 1},
        },
    )

    assert payload == {
        "status": "success",
        "checked_at": "now",
        "ok": True,
        "run_ref": "real-run-ref",
        "intelligence_run_id": 123,
        "engine_status": "completed",
        "idempotent": True,
        "signals": 1,
        "skipped": 1,
        "dataset_unavailable_count": 2,
        "insufficient_history_count": 3,
        "skipped_counts": {"missing": 1},
        "datasets": ["gold_a"],
    }


def test_gold_refresh_error_payload_truncates_exception_for_public_response():
    payload = sync_control_room.gold_refresh_error_payload(
        checked_at="now",
        run_ref="run-ref",
        datasets=["gold_a"],
        exc=RuntimeError("x" * 600),
    )

    assert payload["status"] == "failed"
    assert payload["ok"] is False
    assert payload["run_ref"] == "run-ref"
    assert len(payload["error"]) == 500
