from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from functools import partial

import requests
from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.utils.trigger_rule import TriggerRule

from agent_runner_outcome import require_registry_save
from runtime_security_context import build_pipeline_run_context
from successfactors_daily_schedule import (
    DAILY_DAG_ID,
    RECORD_ENTITY,
    SCHEDULE_CRON_UTC,
    STALE_RUN_MESSAGE,
    discover_targets as _discover_targets,
    reserve_extract_all,
    run_outcome,
    run_window,
    trigger_targets,
)


AIRFLOW_URL = os.environ.get("AIRFLOW_URL", "http://airflow:8080")
AIRFLOW_USER = (
    os.environ.get("AIRFLOW_USER") or os.environ.get("AIRFLOW_ADMIN_USER") or "admin"
)
AIRFLOW_PASS = (
    os.environ.get("AIRFLOW_PASSWORD")
    or os.environ.get("AIRFLOW_ADMIN_PASSWORD")
    or "admin"
)
MCP_INFRA_URL = os.environ.get("MCP_INFRA_URL", "http://mcp-infra:8010")
CONSOLE_URL = os.environ.get("CONSOLE_INTERNAL_URL") or os.environ.get(
    "CONSOLE_URL", "http://console:8000"
)


def _is_production() -> bool:
    return os.environ.get("APP_ENV", "production").strip().lower() in {"production", "prod"}


def _internal_key(env_name: str) -> str:
    key = os.environ.get(env_name, "")
    if key:
        return key
    if not _is_production():
        legacy = os.environ.get("INTERNAL_API_KEY", "")
        if legacy:
            return legacy
    raise RuntimeError(f"{env_name} missing; internal authentication unavailable")


def _headers(target: str) -> dict[str, str]:
    return {
        "X-Internal-Service": "airflow",
        "X-API-Key": _internal_key(f"INTERNAL_API_KEY_AIRFLOW_TO_{target}"),
    }


dag = DAG(
    dag_id=DAILY_DAG_ID,
    default_args={"owner": "platform", "retries": 0},
    description="Extracción diaria incremental de SuccessFactors (06:00 Ciudad de México)",
    schedule_interval=SCHEDULE_CRON_UTC,
    start_date=datetime(2026, 10, 1, tzinfo=timezone.utc),
    tags=["platform", "scheduler", "sap_successfactors"],
    catchup=False,
    max_active_runs=1,
    is_paused_upon_creation=True,
)


def discover_targets(**context):
    window = run_window(context)
    if window["late"]:
        discovery = {"status": "late_catchup", "targets": [], "scope_failures": 0}
    else:
        discovery = _discover_targets(console_url=CONSOLE_URL, headers=_headers("CONSOLE"))
    discovery["day"] = window["day"]
    context["ti"].xcom_push(key="discovery", value=discovery)
    return len(discovery["targets"])


def trigger_extractions(**context):
    discovery = context["ti"].xcom_pull(task_ids="discover_targets", key="discovery") or {}
    day = discovery.get("day") or run_window(context)["day"]
    results = trigger_targets(
        discovery.get("targets") or [],
        day=day,
        base_url=AIRFLOW_URL,
        username=AIRFLOW_USER,
        password=AIRFLOW_PASS,
        reserve=partial(
            reserve_extract_all, mcp_url=MCP_INFRA_URL, headers=_headers("MCP_INFRA")
        ),
    )
    return {"day": day, "results": results}


def record_run(**context):
    discovery = context["ti"].xcom_pull(task_ids="discover_targets", key="discovery")
    invocation = context["ti"].xcom_pull(task_ids="trigger_extractions") or {}
    results = invocation.get("results") if isinstance(invocation, dict) else []
    results = results if isinstance(results, list) else []
    status, reason = run_outcome(discovery, results)
    day = (
        discovery.get("day") if isinstance(discovery, dict) else None
    ) or run_window(context)["day"]
    args = {
        "dag_id": DAILY_DAG_ID,
        "cartridge_id": "platform",
        "entity": RECORD_ENTITY,
        "run_id": f"{DAILY_DAG_ID}:{context['run_id']}",
        "airflow_dag_run_id": context["run_id"],
        "mode": "scheduled",
        "status": status,
        "started_at": context["logical_date"].isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "extra": {
            "day": day,
            "reason": reason,
            "discovery_status": (discovery or {}).get("status") if isinstance(discovery, dict) else None,
            "scope_failures": (discovery or {}).get("scope_failures") if isinstance(discovery, dict) else None,
            "results": results,
        },
    }
    if reason:
        args["error_message"] = STALE_RUN_MESSAGE if reason == "stale_active_run" else reason
    response = requests.post(
        f"{MCP_INFRA_URL}/mcp/invoke",
        headers=_headers("MCP_INFRA"),
        json={
            "tool": "pipeline_run_save",
            "args": args,
            "security_context": build_pipeline_run_context(args),
        },
        timeout=15,
    )
    require_registry_save(response, expected_status=status)
    if status in {"blocked", "failed", "partial"}:
        raise RuntimeError(f"daily SuccessFactors extraction finished {status}")
    return status


t_discover = PythonOperator(
    task_id="discover_targets",
    python_callable=discover_targets,
    execution_timeout=timedelta(minutes=5),
    dag=dag,
)
t_trigger = PythonOperator(
    task_id="trigger_extractions",
    python_callable=trigger_extractions,
    execution_timeout=timedelta(minutes=10),
    dag=dag,
)
t_record = PythonOperator(
    task_id="record_run",
    python_callable=record_run,
    trigger_rule=TriggerRule.ALL_DONE,
    dag=dag,
)

t_discover >> t_trigger >> t_record
