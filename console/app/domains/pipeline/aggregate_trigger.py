from __future__ import annotations

from typing import Any


async def trigger_sync_aggregate_extract_all(
    *,
    cartridge: str,
    mode: str,
    target: str,
    conn_id: str | None,
    run_id: str,
    user: dict[str, Any] | None,
    sync_extract_all_dags: dict[str, str],
    sync_aggregate_entity: str,
    apply_user_scope_to_dag_conf: Any,
    dag_run_id_from_idempotency_key: Any,
    trigger_airflow_extract_dag: Any,
    record_dag_pipeline_trigger: Any,
) -> dict[str, Any] | None:
    dag_id = sync_extract_all_dags.get(cartridge)
    if not dag_id:
        return None

    conf: dict[str, Any] = {
        "cartridge_id": cartridge,
        "mode": mode,
        "target": target,
        "idempotency_key": run_id,
    }
    if conn_id:
        conf["conn_id"] = conn_id
    conf = apply_user_scope_to_dag_conf(conf, user)
    requested_dag_run_id = dag_run_id_from_idempotency_key(dag_id, run_id)
    result = await trigger_airflow_extract_dag(
        dag_id, conf, user, requested_dag_run_id
    )
    if result.get("error"):
        return {
            "cartridge": cartridge,
            "triggered": [],
            "errors": [
                {
                    "entity": sync_aggregate_entity,
                    "status_code": 502,
                    "error": f"Airflow trigger failed: {result['error']}",
                }
            ],
            "count": 0,
            "error_count": 1,
            "trigger_strategy": "aggregate_dag",
        }

    dag_run_id = (
        result.get("dag_run_id") or result.get("run_id") or requested_dag_run_id
    )
    await record_dag_pipeline_trigger(
        cartridge=cartridge,
        entity=sync_aggregate_entity,
        dag_id=dag_id,
        dag_run_id=dag_run_id,
        mode=conf.get("mode", mode),
        status=result.get("state") or "queued",
        conf=conf,
        tenant_id=conf.get("tenant_id"),
        workspace_id=conf.get("workspace_id"),
    )
    triggered = {
        "entity": sync_aggregate_entity,
        "job_id": dag_run_id,
        "dag_run_id": dag_run_id,
        "dag_id": dag_id,
        "state": result.get("state"),
        "result": result,
    }
    return {
        "cartridge": cartridge,
        "triggered": [triggered],
        "errors": [],
        "count": 1,
        "error_count": 0,
        "trigger_strategy": "aggregate_dag",
    }
