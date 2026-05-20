"""
DAG: agent_runner

Cada 5 minutos: revisa la tabla `agents`, identifica los que tienen
`extra.schedule.cron` y deberían ejecutarse en este intervalo, y los
invoca contra el endpoint `/api/agents/<id>/invoke/scheduled` del
console (con un token compartido).

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

import json
import os
from datetime import datetime, timedelta, timezone

import requests
from airflow import DAG
from airflow.models import Variable
from airflow.operators.python import PythonOperator


# ── Config ───────────────────────────────────────────────────────────────────

CARTRIDGE_ID  = "platform"
ENTITY        = "AgentRunner"
AGENT_RUNNER_DAG_ID = "agent_runner"
CONSOLE_URL   = os.environ.get("CONSOLE_URL", "http://mode_console:8000")
MCP_INFRA_URL = "http://mcp-infra:8010"
MCP_INFRA_KEY = (
    os.environ.get("INTERNAL_API_KEY_AIRFLOW_TO_MCP_INFRA")
    or os.environ.get("INTERNAL_API_KEY", "")
)

# Shared token so this DAG can call /invoke/scheduled without a user cookie.
# Set in the App EC2's .env as AGENT_RUNNER_TOKEN, propagated to mode_airflow
# and mode_console via docker-compose env.
RUNNER_TOKEN  = os.environ.get("AGENT_RUNNER_TOKEN", "")
CONSOLE_INTERNAL_KEY = (
    os.environ.get("INTERNAL_API_KEY_AIRFLOW_TO_CONSOLE")
    or os.environ.get("INTERNAL_API_KEY", "")
)

POSTGRES_DSN  = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres:postgres@postgres:5432/modecissions",
).replace("postgresql+psycopg2://", "postgresql://")

# DAG runs every 5 min. We look back over this same window so a cron that
# fires anywhere in [previous_run, now) gets dispatched exactly once per fire.
INTERVAL_MIN  = 5


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
    is_paused_upon_creation=False,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _pg():
    import psycopg2
    import psycopg2.extras
    conn = psycopg2.connect(POSTGRES_DSN)
    return conn


def _mcp_headers() -> dict[str, str]:
    return {"x-api-key": MCP_INFRA_KEY, "x-internal-service": "airflow"}


def _cron_fires_in_window(cron_expr: str, tz_name: str,
                          window_start: datetime, window_end: datetime) -> bool:
    """True if the cron expression fires at least once in [window_start, window_end)."""
    try:
        from croniter import croniter
    except Exception:                                              # noqa: BLE001
        return False

    # croniter wants a naive or aware base; build aware in tz, compare in UTC
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(tz_name) if tz_name else timezone.utc
    except Exception:                                              # noqa: BLE001
        tz = timezone.utc

    base = window_start.astimezone(tz) - timedelta(seconds=1)
    try:
        itr = croniter(cron_expr, base)
    except Exception:                                              # noqa: BLE001
        return False

    next_fire = itr.get_next(datetime)
    if next_fire.tzinfo is None:
        next_fire = next_fire.replace(tzinfo=tz)
    return window_start <= next_fire.astimezone(timezone.utc) < window_end


# ── Task 1 · find_due_agents ─────────────────────────────────────────────────

def find_due_agents(**context):
    """Pick agents whose cron expression fires inside this scheduling window."""
    logical_date = context["logical_date"]  # tz-aware UTC
    window_end   = logical_date + timedelta(minutes=INTERVAL_MIN)

    print(f"[agent_runner] window UTC [{logical_date.isoformat()}, {window_end.isoformat()})")

    conn = _pg()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, cartridge_id, slug, name, extra "
                "FROM agents WHERE is_active = TRUE AND extra ? 'schedule'"
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    due = []
    for agent_id, cartridge_id, slug, name, extra in rows:
        if not isinstance(extra, dict):
            try:    extra = json.loads(extra)
            except: continue
        sched = (extra or {}).get("schedule") or {}
        if not sched.get("cron"):
            continue
        if sched.get("enabled") is False:
            continue
        if not _cron_fires_in_window(
            sched["cron"],
            sched.get("tz") or "UTC",
            logical_date, window_end,
        ):
            continue
        due.append({
            "id":           str(agent_id),
            "cartridge_id": cartridge_id,
            "slug":         slug,
            "name":         name,
            "prompt":       sched.get("prompt") or "Ejecuta tu tarea programada.",
        })

    print(f"[agent_runner] {len(due)} due: {[d['slug'] for d in due]}")
    context["ti"].xcom_push(key="due", value=due)
    return len(due)


# ── Task 2 · invoke_each ─────────────────────────────────────────────────────

def invoke_each(**context):
    due = context["ti"].xcom_pull(task_ids="find_due_agents", key="due") or []
    if not due:
        print("[agent_runner] nothing to invoke")
        return {"invoked": 0, "results": []}
    if not RUNNER_TOKEN:
        print("[agent_runner] AGENT_RUNNER_TOKEN is empty — refusing to call console")
        return {"invoked": 0, "error": "no_token", "candidates": [d["slug"] for d in due]}

    results = []
    for agent in due:
        url = f"{CONSOLE_URL}/api/agents/{agent['id']}/invoke/scheduled"
        try:
            r = requests.post(
                url,
                json={"message": agent["prompt"]},
                headers={"X-Agent-Runner-Token": RUNNER_TOKEN,
                         "X-Api-Key": CONSOLE_INTERNAL_KEY,
                         "X-Internal-Service": "airflow",
                         "Content-Type": "application/json"},
                timeout=600,
            )
            results.append({
                "slug":     agent["slug"],
                "status":   r.status_code,
                "run_id":   (r.json() or {}).get("run_id") if r.status_code < 400 else None,
                "preview":  (r.text or "")[:200],
            })
        except Exception as exc:                                   # noqa: BLE001
            results.append({"slug": agent["slug"], "status": "exception", "error": str(exc)})

    ok = sum(1 for r in results if isinstance(r.get("status"), int) and r["status"] < 400)
    print(f"[agent_runner] invoked {ok}/{len(results)}: {results}")
    return {"invoked": ok, "results": results}


# ── Task 3 · record_run ──────────────────────────────────────────────────────

def _pipeline_status(inv: dict) -> str:
    if inv.get("error"):
        return "failed"
    results = inv.get("results") or []
    if not results:
        return "success"
    ok = sum(1 for r in results if isinstance(r.get("status"), int) and r["status"] < 400)
    if ok == len(results):
        return "success"
    return "partial" if ok else "failed"


def record_run(**context):
    inv = context["ti"].xcom_pull(task_ids="invoke_each") or {}
    logical_date = context["logical_date"]
    started = logical_date.isoformat()
    ended_dt = datetime.now(timezone.utc)
    ended   = ended_dt.isoformat()
    duration = max(0.0, (ended_dt - logical_date).total_seconds())
    payload = {
        "tool": "pipeline_run_save",
        "args": {
            "cartridge_id": CARTRIDGE_ID,
            "entity":       ENTITY,
            "dag_id":       AGENT_RUNNER_DAG_ID,
            "run_id":       f"{AGENT_RUNNER_DAG_ID}:{context['run_id']}",
            "airflow_dag_run_id": context["run_id"],
            "mode":         "scheduled",
            "status":       _pipeline_status(inv),
            "started_at":   started,
            "finished_at":  ended,
            "duration_seconds": duration,
            "extra":        inv,
        },
    }
    try:
        r = requests.post(
            f"{MCP_INFRA_URL}/mcp/invoke",
            json=payload,
            headers=_mcp_headers(),
            timeout=15,
        )
        print(f"[agent_runner] pipeline_run_save → {r.status_code}: {r.text[:200]}")
        try:
            data = r.json()
        except Exception:
            data = {}
        if r.status_code >= 400 or data.get("error"):
            raise RuntimeError(f"pipeline_run_save failed: {r.status_code} {r.text[:300]}")
    except Exception as exc:                                       # noqa: BLE001
        print(f"[agent_runner] pipeline_run_save failed: {exc}")
        raise


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
