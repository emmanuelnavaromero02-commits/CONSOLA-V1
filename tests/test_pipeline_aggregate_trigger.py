from __future__ import annotations

import pytest

from app.domains.pipeline.aggregate_trigger import (
    trigger_sync_aggregate_extract_all,
)


@pytest.mark.anyio
async def test_aggregate_trigger_returns_none_without_configured_dag():
    payload = await trigger_sync_aggregate_extract_all(
        cartridge="unknown",
        mode="incremental",
        target="all",
        conn_id=None,
        run_id="sync-now-1",
        user=None,
        sync_extract_all_dags={},
        sync_aggregate_entity="__extract_all__",
        apply_user_scope_to_dag_conf=lambda conf, user: conf,
        dag_run_id_from_idempotency_key=lambda dag_id, key: f"{dag_id}:{key}",
        trigger_airflow_extract_dag=None,
        record_dag_pipeline_trigger=None,
    )

    assert payload is None


@pytest.mark.anyio
async def test_aggregate_trigger_records_successful_airflow_run():
    trigger_calls = []
    record_calls = []

    def apply_scope(conf, _user):
        return {**conf, "tenant_id": "tenant-1", "workspace_id": "workspace-1"}

    async def trigger_airflow(dag_id, conf, user, requested_dag_run_id):
        trigger_calls.append((dag_id, conf, user, requested_dag_run_id))
        return {"dag_run_id": "dag-run-1", "state": "queued"}

    async def record_trigger(**kwargs):
        record_calls.append(kwargs)

    payload = await trigger_sync_aggregate_extract_all(
        cartridge="sap_successfactors",
        mode="incremental",
        target="all",
        conn_id="tenant_sf",
        run_id="sync-now-1",
        user={"sub": "user-1"},
        sync_extract_all_dags={"sap_successfactors": "sf_extract_all"},
        sync_aggregate_entity="__extract_all__",
        apply_user_scope_to_dag_conf=apply_scope,
        dag_run_id_from_idempotency_key=lambda dag_id, key: f"{dag_id}:{key}",
        trigger_airflow_extract_dag=trigger_airflow,
        record_dag_pipeline_trigger=record_trigger,
    )

    assert payload["count"] == 1
    assert payload["error_count"] == 0
    assert payload["triggered"][0]["dag_run_id"] == "dag-run-1"
    assert trigger_calls[0][0] == "sf_extract_all"
    assert trigger_calls[0][3] == "sf_extract_all:sync-now-1"
    assert trigger_calls[0][1]["conn_id"] == "tenant_sf"
    assert record_calls == [
        {
            "cartridge": "sap_successfactors",
            "entity": "__extract_all__",
            "dag_id": "sf_extract_all",
            "dag_run_id": "dag-run-1",
            "mode": "incremental",
            "status": "queued",
            "conf": trigger_calls[0][1],
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
        }
    ]


@pytest.mark.anyio
async def test_aggregate_trigger_returns_functional_error_payload():
    async def trigger_airflow(*_args):
        return {"error": "airflow unavailable"}

    payload = await trigger_sync_aggregate_extract_all(
        cartridge="sap_successfactors",
        mode="incremental",
        target="all",
        conn_id=None,
        run_id="sync-now-1",
        user=None,
        sync_extract_all_dags={"sap_successfactors": "sf_extract_all"},
        sync_aggregate_entity="__extract_all__",
        apply_user_scope_to_dag_conf=lambda conf, user: conf,
        dag_run_id_from_idempotency_key=lambda dag_id, key: f"{dag_id}:{key}",
        trigger_airflow_extract_dag=trigger_airflow,
        record_dag_pipeline_trigger=None,
    )

    assert payload == {
        "cartridge": "sap_successfactors",
        "triggered": [],
        "errors": [
            {
                "entity": "__extract_all__",
                "status_code": 502,
                "error": "Airflow trigger failed: airflow unavailable",
            }
        ],
        "count": 0,
        "error_count": 1,
        "trigger_strategy": "aggregate_dag",
    }
