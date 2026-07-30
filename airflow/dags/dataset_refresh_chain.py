"""RLS-scoped Bronze→Silver→Gold dependency refresh orchestrator."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

import requests
from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.utils.trigger_rule import TriggerRule

from dataset_refresh_graph import _required_scope, resolve_chain as _resolve_chain
from dataset_refresh_materialize import materialize_in_order as _materialize_in_order


CARTRIDGE_ID = "platform"
ENTITY = "DatasetRefreshChain"
REFINEMENT_URL = os.environ.get("REFINEMENT_URL", "http://refinement:8500")
MCP_INFRA_URL = os.environ.get("MCP_INFRA_URL", "http://mcp-infra:8010")
CONSOLE_URL = os.environ.get("CONSOLE_INTERNAL_URL") or os.environ.get(
    "CONSOLE_URL", "http://console:8000"
)
POSTGRES_DSN = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres:postgres@postgres:5432/modecissions",
).replace("postgresql+psycopg2://", "postgresql://")


default_args = {
    "owner": "platform",
    "retries": 1,
    "retry_delay": timedelta(minutes=2),
}

dag = DAG(
    dag_id="dataset_refresh_chain",
    default_args=default_args,
    description="Propaga materializaciones RLS-scoped por datasets.sources.",
    schedule_interval=None,
    start_date=datetime(2026, 5, 1),
    tags=["platform", "orchestrator", "refresh"],
    catchup=False,
    max_active_runs=4,
    params={
        "seed_raw": {"type": "string", "default": ""},
        "seed_dataset": {"type": "string", "default": ""},
        "max_depth": {"type": "integer", "default": 10},
    },
)


def _is_production() -> bool:
    return os.environ.get("APP_ENV", "production").strip().lower() in {
        "production",
        "prod",
    }


def _internal_key(env_name: str) -> str:
    key = os.environ.get(env_name, "")
    if key:
        return key
    if not _is_production():
        legacy = os.environ.get("INTERNAL_API_KEY", "")
        if legacy:
            return legacy
    raise RuntimeError(f"{env_name} missing; internal authentication unavailable")


def _internal_headers(target: str, ctx: dict | None = None) -> dict[str, str]:
    # The exact pair key is INTERNAL_API_KEY_AIRFLOW_TO_{target}.
    headers = {
        "X-Internal-Service": "airflow",
        "X-API-Key": _internal_key(f"INTERNAL_API_KEY_AIRFLOW_TO_{target}"),
    }
    if ctx:
        headers["X-Request-ID"] = (
            f"airflow:dataset_refresh_chain:{ctx.get('run_id', 'manual')}"
        )
    return headers


def resolve_chain(**ctx):
    return _resolve_chain(ctx, postgres_dsn=POSTGRES_DSN)


def materialize_in_order(**ctx):
    return _materialize_in_order(
        ctx,
        postgres_dsn=POSTGRES_DSN,
        refinement_url=REFINEMENT_URL,
        headers=_internal_headers,
    )


def _successful_materialized_datasets(results: list[dict]) -> list[str]:
    return sorted(
        {
            str(item.get("name") or "").strip()
            for item in results
            if isinstance(item, dict) and item.get("ok") and item.get("name")
        }
    )


def _skip_intelligence_for_cartridge(cartridge_id: str) -> bool:
    return cartridge_id in {"banxico", "inegi", "sec_edgar"}


def _trigger_gold_refresh_intelligence(
    *,
    ctx: dict[str, Any],
    tenant_id: str,
    workspace_id: str,
    cartridge_id: str,
    pipeline_run_id: str,
    status: str,
    datasets: list[str],
    finished_at: str,
) -> None:
    conf = (ctx.get("dag_run").conf if ctx.get("dag_run") else {}) or {}
    if bool(conf.get("skip_intelligence")) or _skip_intelligence_for_cartridge(
        cartridge_id
    ):
        return
    if not datasets:
        return
    payload = {
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "cartridge_id": cartridge_id,
        "airflow_dag_run_id": ctx["run_id"],
        "pipeline_run_id": pipeline_run_id,
        "materialization_status": status,
        "datasets": datasets,
        "finished_at": finished_at,
    }
    try:
        response = requests.post(
            f"{CONSOLE_URL.rstrip('/')}/internal/intelligence/gold-refresh",
            headers=_internal_headers("CONSOLE", ctx),
            json=payload,
            timeout=45,
        )
        if response.status_code >= 400:
            raise RuntimeError("Gold intelligence trigger rejected")
    except Exception as exc:
        raise RuntimeError("Gold intelligence trigger unavailable") from exc


def _materialization_result(ctx: dict[str, Any]) -> tuple[dict, bool]:
    result = (
        ctx["ti"].xcom_pull(task_ids="materialize_in_order", key="result")
        or ctx["ti"].xcom_pull(task_ids="materialize_in_order")
        or {}
    )
    dag_run = ctx.get("dag_run")
    task = dag_run.get_task_instance("materialize_in_order") if dag_run else None
    failed = bool(task and str(getattr(task, "state", "") or "") != "success")
    if not result and failed:
        return {"materialized": 0, "results": [], "error": "task_failed"}, True
    return result, failed


def record_run(**ctx):
    conf = (ctx.get("dag_run").conf if ctx.get("dag_run") else {}) or {}
    allow_partial = bool(conf.get("allow_partial"))
    invocation, task_failed = _materialization_result(ctx)
    tenant_id, workspace_id = _required_scope(conf)
    cartridge = str(
        ctx["ti"].xcom_pull(task_ids="resolve_chain", key="cartridge_id")
        or conf.get("cartridge_id")
        or ""
    )
    results = invocation.get("results") or []
    completed = int(invocation.get("materialized") or 0)
    total = len(results)
    status = "success" if total == completed else "partial" if completed else "failed"
    if total == 0 and not task_failed and not invocation.get("error"):
        status = "success"
    finished_at = datetime.now(timezone.utc).isoformat()
    pipeline_run_id = f"dataset_refresh_chain:{ctx['run_id']}"
    response = requests.post(
        f"{MCP_INFRA_URL}/mcp/invoke",
        headers=_internal_headers("MCP_INFRA", ctx),
        json={
            "tool": "pipeline_run_save",
            "args": {
                "dag_id": "dataset_refresh_chain",
                "cartridge_id": cartridge,
                "entity": ENTITY,
                "run_id": pipeline_run_id,
                "airflow_dag_run_id": ctx["run_id"],
                "mode": "refresh",
                "status": status,
                "started_at": ctx["logical_date"].isoformat(),
                "finished_at": finished_at,
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "project_id": conf.get("project_id"),
                "extra": invocation,
            },
        },
        timeout=15,
    )
    body = response.json() if response.status_code < 400 else {}
    if response.status_code >= 400 or body.get("error"):
        raise RuntimeError("pipeline run registry unavailable")
    if status == "success" or (status == "partial" and allow_partial):
        _trigger_gold_refresh_intelligence(
            ctx=ctx,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            cartridge_id=cartridge,
            pipeline_run_id=pipeline_run_id,
            status=status,
            datasets=_successful_materialized_datasets(results),
            finished_at=finished_at,
        )
    if status != "success" and not allow_partial:
        raise RuntimeError("dataset_refresh_chain recorded a failed run")


t_resolve = PythonOperator(
    task_id="resolve_chain", python_callable=resolve_chain, dag=dag
)
t_mat = PythonOperator(
    task_id="materialize_in_order", python_callable=materialize_in_order, dag=dag
)
t_rec = PythonOperator(
    task_id="record_run",
    python_callable=record_run,
    trigger_rule=TriggerRule.ALL_DONE,
    dag=dag,
)

t_resolve >> t_mat >> t_rec
