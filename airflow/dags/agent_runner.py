"""
DAG: agent_runner

Cada 5 minutos pide a Console una enumeración server-owned y RLS-scoped de
agentes cuyo `extra.schedule.cron` cae en el intervalo. Después invoca cada
capability contra `/api/agents/<id>/invoke/scheduled`.

El mensaje enviado al agente es `extra.schedule.prompt` si existe,
de lo contrario "ejecuta tu tarea programada".

Para que un agente se ejecute aquí:
  - `extra.schedule.cron`: expresión cron válida (puede incluir tz vía
    `extra.schedule.tz`, default UTC).
  - opcional `extra.schedule.prompt`: instrucción concreta para esa
    invocación programada.
  - opcional `extra.schedule.enabled = false` para pausar sin borrar.

Resultado: cada invocación crea un row en `agent_runs` (lo hace el
runtime del console) y este DAG además registra un row en `pipeline_runs`
para que la UI muestre la última corrida del agent_runner.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta

import requests
from airflow import DAG
from airflow.operators.python import PythonOperator

from agent_runner_outcome import require_scheduled_invocation
from agent_runner_record import record_agent_runner_run


# ── Config ───────────────────────────────────────────────────────────────────

CARTRIDGE_ID = "platform"
ENTITY = "AgentRunner"
AGENT_RUNNER_DAG_ID = "agent_runner"
CONSOLE_URL = os.environ.get("CONSOLE_URL", "http://mode_console:8000")
MCP_INFRA_URL = "http://mcp-infra:8010"


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
    raise RuntimeError(
        f"{env_name} missing; legacy INTERNAL_API_KEY fallback is disabled in production"
    )


def _pause_scheduled_dag_on_creation() -> bool:
    value = (
        os.environ.get("AIRFLOW_DAGS_ARE_PAUSED_AT_CREATION")
        or os.environ.get("AIRFLOW__CORE__DAGS_ARE_PAUSED_AT_CREATION")
        or ""
    )
    return value.strip().lower() in {"1", "true", "yes", "on"}


# Shared token so this DAG can call /invoke/scheduled without a user cookie.
# Set in the App EC2's .env as AGENT_RUNNER_TOKEN, propagated to mode_airflow
# and mode_console via docker-compose env.
RUNNER_TOKEN = os.environ.get("AGENT_RUNNER_TOKEN", "")

# DAG runs every 5 min. We look back over this same window so a cron that
# fires anywhere in [previous_run, now) gets dispatched exactly once per fire.
INTERVAL_MIN = 5


default_args = {
    "owner": "platform",
    "retries": 1,
    "retry_delay": timedelta(minutes=1),
}


dag = DAG(
    dag_id="agent_runner",
    default_args=default_args,
    description="Invoca agentes cuya extra.schedule.cron caiga dentro del intervalo actual",
    schedule_interval=f"*/{INTERVAL_MIN} * * * *",
    start_date=datetime(2026, 5, 1),
    tags=["platform", "agents", "scheduled"],
    catchup=False,
    max_active_runs=1,
    is_paused_upon_creation=_pause_scheduled_dag_on_creation(),
)


def _mcp_headers() -> dict[str, str]:
    return {
        "x-api-key": _internal_key("INTERNAL_API_KEY_AIRFLOW_TO_MCP_INFRA"),
        "x-internal-service": "airflow",
    }


# ── Task 1 · find_due_agents ─────────────────────────────────────────────────


def find_due_agents(**context):
    """Pick agents whose cron expression fires inside this scheduling window."""
    logical_date = context["logical_date"]  # tz-aware UTC
    window_end = logical_date + timedelta(minutes=INTERVAL_MIN)

    url = f"{CONSOLE_URL.rstrip('/')}/api/operations/internal/agent-runner/due"
    try:
        response = requests.post(
            url,
            headers={
                "X-Api-Key": _internal_key("INTERNAL_API_KEY_AIRFLOW_TO_CONSOLE"),
                "X-Internal-Service": "airflow",
                "Content-Type": "application/json",
            },
            json={
                "window_start": logical_date.isoformat(),
                "window_end": window_end.isoformat(),
            },
            timeout=30,
        )
        data = response.json() if response.status_code < 400 else {}
        if response.status_code >= 400 or not isinstance(data, dict):
            raise RuntimeError("scheduled discovery rejected")
    except Exception as exc:
        raise RuntimeError("scheduled discovery unavailable") from exc
    if data.get("status") not in {"ready", "partial", "no_eligible_workspaces"}:
        raise RuntimeError("scheduled discovery returned an invalid state")
    due = data.get("due") if isinstance(data.get("due"), list) else []
    context["ti"].xcom_push(key="due", value=due)
    context["ti"].xcom_push(
        key="discovery",
        value={
            "status": data.get("status"),
            "workspaces": int(data.get("workspaces") or 0),
            "scope_failures": len(data.get("failures") or []),
        },
    )
    return len(due)


# ── Task 2 · invoke_each ─────────────────────────────────────────────────────


def invoke_each(**context):
    due = context["ti"].xcom_pull(task_ids="find_due_agents", key="due") or []
    discovery = (
        context["ti"].xcom_pull(task_ids="find_due_agents", key="discovery") or {}
    )
    if not due:
        return {
            "invoked": 0,
            "results": [],
            "operational_status": discovery.get("status") or "no_due_agents",
            "workspace_count": discovery.get("workspaces") or 0,
            "scope_failures": discovery.get("scope_failures") or 0,
        }
    if not RUNNER_TOKEN:
        raise RuntimeError("scheduled runner authentication unavailable")

    results = []
    for agent in due:
        url = f"{CONSOLE_URL}/api/agents/{agent['id']}/invoke/scheduled"
        response = None
        try:
            response = requests.post(
                url,
                json={
                    "message": agent["prompt"],
                    "tenant_id": agent.get("tenant_id"),
                    "workspace_id": agent.get("workspace_id"),
                    "scheduled_fire_at": agent.get("scheduled_fire_at"),
                    "schedule_key": agent.get("schedule_key") or "default",
                    "airflow_dag_run_id": context["run_id"],
                },
                headers={
                    "X-Agent-Runner-Token": RUNNER_TOKEN,
                    "X-Api-Key": _internal_key("INTERNAL_API_KEY_AIRFLOW_TO_CONSOLE"),
                    "X-Internal-Service": "airflow",
                    "Content-Type": "application/json",
                },
                timeout=600,
            )
            results.append(require_scheduled_invocation(response))
        except Exception as exc:  # noqa: BLE001
            code = getattr(response, "status_code", "exception")
            results.append(
                {"ok": False, "status": code, "error_code": type(exc).__name__}
            )

    invoked = sum(
        1
        for item in results
        if item.get("ok") is True and item.get("duplicate") is False
    )
    return {
        "invoked": invoked,
        "results": results,
        "operational_status": discovery.get("status") or "ready",
        "workspace_count": discovery.get("workspaces") or 0,
        "scope_failures": discovery.get("scope_failures") or 0,
    }


def record_run(**context):
    return record_agent_runner_run(
        context,
        mcp_url=MCP_INFRA_URL,
        headers=_mcp_headers,
        cartridge_id=CARTRIDGE_ID,
        entity=ENTITY,
        dag_id=AGENT_RUNNER_DAG_ID,
    )


# ── DAG wiring ───────────────────────────────────────────────────────────────

t_find = PythonOperator(
    task_id="find_due_agents",
    python_callable=find_due_agents,
    dag=dag,
)
t_inv = PythonOperator(
    task_id="invoke_each",
    python_callable=invoke_each,
    dag=dag,
)
t_rec = PythonOperator(
    task_id="record_run",
    python_callable=record_run,
    dag=dag,
)

t_find >> t_inv >> t_rec
