from __future__ import annotations

import pytest

from app.domains.pipeline.control_room_refresh import (
    run_sync_control_room_gold_refresh,
    run_sync_control_room_status,
)
from app.services import sync_control_room


@pytest.mark.anyio
async def test_control_room_gold_refresh_skips_without_datasets():
    payload = await run_sync_control_room_gold_refresh(
        cartridge="sap_successfactors",
        row={"run_id": "sync-now-1"},
        child_rows=[],
        gold_refresh_summary={},
        user=None,
        build_security_context=lambda user: {},
        sync_gold_refresh_dataset_names=lambda summary: [],
        sync_gold_refresh_airflow_run_id=lambda row, child_rows: "aggregate-run",
        sync_control_room=sync_control_room,
    )

    assert payload["status"] == "skipped"
    assert payload["datasets"] == []


@pytest.mark.anyio
async def test_control_room_gold_refresh_persists_intelligence_payload():
    calls = []

    async def run_intelligence(user, payload, persist=False):
        calls.append({"user": user, "payload": payload, "persist": persist})
        return {
            "status": "completed",
            "run_ref": payload["run_ref"],
            "intelligence_run_id": "intel-1",
            "signals": [{"id": "signal-1"}],
            "skipped": [],
        }

    payload = await run_sync_control_room_gold_refresh(
        cartridge="sap_successfactors",
        row={"run_id": "sync-now-1"},
        child_rows=[{"entity": "__extract_all__", "run_id": "aggregate-run"}],
        gold_refresh_summary={
            "status": "success",
            "results": [{"name": "sap_successfactors_talent_signals"}],
        },
        user={"sub": "user-1"},
        build_security_context=lambda user: {
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
        },
        sync_gold_refresh_dataset_names=lambda summary: [
            item["name"] for item in summary["results"]
        ],
        sync_gold_refresh_airflow_run_id=lambda row, child_rows: child_rows[0][
            "run_id"
        ],
        sync_control_room=sync_control_room,
        run_intelligence=run_intelligence,
    )

    assert payload["status"] == "success"
    assert payload["signals"] == 1
    assert payload["run_ref"] == (
        "gold-refresh:workspace-1:sap_successfactors:aggregate-run"
    )
    assert calls == [
        {
            "user": {"sub": "user-1"},
            "persist": True,
            "payload": {
                "cartridge_id": "sap_successfactors",
                "datasets": ["sap_successfactors_talent_signals"],
                "include_external": False,
                "dry_run": False,
                "run_mode": "gold_refresh",
                "run_ref": "gold-refresh:workspace-1:sap_successfactors:aggregate-run",
                "horizon_days": [7, 21],
                "metadata": {
                    "trigger": "sync_now_gold_refresh",
                    "pipeline_run_id": "sync-now-1",
                    "airflow_dag_run_id": "aggregate-run",
                    "materialization_status": "success",
                    "datasets_received": ["sap_successfactors_talent_signals"],
                    "finished_at": None,
                },
            },
        }
    ]


@pytest.mark.anyio
async def test_control_room_status_marks_successfactors_ready_with_kpis():
    class FakeControlRoomService:
        async def refresh_dashboard_state(self, user):
            assert user == {"sub": "user-1"}
            return {
                "meta": {"source_count": 1, "item_count": 1},
                "summary": {"total_items": 1, "data_ready_sources": 1},
            }

        async def sap_successfactors_gold_kpis(self, user):
            assert user == {"sub": "user-1"}
            return {"employee_count": 10}

        async def sap_successfactors_talent_kpis(self, user):
            assert user == {"sub": "user-1"}
            return {"readiness_status": "ready"}

    status = await run_sync_control_room_status(
        cartridge="sap_successfactors",
        bronze_ready=1,
        silver_ready=1,
        gold_ready=1,
        running_children=False,
        control_room_gold_refresh={"status": "success"},
        user={"sub": "user-1"},
        control_room_service=FakeControlRoomService(),
    )

    assert status["ready"] is True
    assert status["checked_at"]
    assert status["snapshot"]["source_count"] == 1
    assert status["snapshot"]["item_count"] == 1
    assert status["update"]["status"] == "success"


@pytest.mark.anyio
async def test_control_room_status_waits_for_successfactors_materialization():
    status = await run_sync_control_room_status(
        cartridge="sap_successfactors",
        bronze_ready=0,
        silver_ready=0,
        gold_ready=0,
        running_children=True,
        control_room_gold_refresh={},
        user=None,
    )

    assert status["ready"] is False
    assert status["checked_at"]
    assert status["snapshot"] == {}
    assert status["update"]["status"] == "queued"


@pytest.mark.anyio
async def test_control_room_status_reports_error_step_safely():
    class BrokenControlRoomService:
        async def refresh_dashboard_state(self, *_args, **_kwargs):
            raise RuntimeError("dashboard offline")

    status = await run_sync_control_room_status(
        cartridge="sap_successfactors",
        bronze_ready=1,
        silver_ready=0,
        gold_ready=0,
        running_children=False,
        control_room_gold_refresh={},
        user=None,
        control_room_service=BrokenControlRoomService(),
    )

    assert status["ready"] is False
    assert status["checked_at"]
    assert status["snapshot"] == {}
    assert status["update"]["status"] == "partial"
    assert "dashboard offline" in status["update"]["error"]
