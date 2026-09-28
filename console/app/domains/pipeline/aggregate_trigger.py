from __future__ import annotations

from typing import Any

from fastapi import HTTPException


def _preflight_error_payload(
    *, cartridge: str, sync_aggregate_entity: str, exc: HTTPException
) -> dict[str, Any]:
    detail = exc.detail if isinstance(exc.detail, dict) else {}
    error: dict[str, Any] = {
        "entity": sync_aggregate_entity,
        "status_code": exc.status_code,
        "error": str(
            detail.get("public_message") or detail.get("message") or exc.detail
        ),
    }
    if detail.get("reason"):
        error["reason"] = str(detail["reason"])
    return {
        "cartridge": cartridge,
        "triggered": [],
        "errors": [error],
        "count": 0,
        "error_count": 1,
        "trigger_strategy": "aggregate_dag",
    }


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
    preflight: Any | None = None,
) -> dict[str, Any] | None:
    dag_id = sync_extract_all_dags.get(cartridge)
    if not dag_id:
        return None

    automation: dict[str, Any] | None = None
    if preflight is not None:
        try:
            checked = await preflight(dag_id, user, mode=mode, target=target)
        except HTTPException as exc:
            if exc.status_code != 409:
                raise
            return _preflight_error_payload(
                cartridge=cartridge,
                sync_aggregate_entity=sync_aggregate_entity,
                exc=exc,
            )
        if checked is not None and getattr(checked, "checked", False):
            automation = checked.automation()
        reuse_run_id = getattr(checked, "reuse_run_id", None) if checked else None
        if reuse_run_id:
            reused = {
                "entity": sync_aggregate_entity,
                "job_id": reuse_run_id,
                "dag_run_id": reuse_run_id,
                "dag_id": dag_id,
                "state": None,
                "reused": True,
            }
            payload: dict[str, Any] = {
                "cartridge": cartridge,
                "triggered": [reused],
                "errors": [],
                "count": 1,
                "error_count": 0,
                "trigger_strategy": "aggregate_dag",
                "reused": True,
                "reason": "active_extract_all_run",
            }
            if automation is not None:
                payload["automation"] = automation
            return payload

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
                    "error": (
                        "No se pudo iniciar la extracción en el orquestador: "
                        f"{result['error']}"
                    ),
                    "reason": "airflow_trigger_failed",
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
    payload = {
        "cartridge": cartridge,
        "triggered": [triggered],
        "errors": [],
        "count": 1,
        "error_count": 0,
        "trigger_strategy": "aggregate_dag",
    }
    if automation is not None:
        payload["automation"] = automation
    return payload
