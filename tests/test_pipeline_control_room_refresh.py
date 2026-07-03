from __future__ import annotations

import pytest

from app.domains.pipeline.control_room_refresh import (
    run_sync_control_room_gold_refresh,
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
