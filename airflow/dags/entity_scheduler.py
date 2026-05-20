"""
DAG: entity_scheduler

Meta-scheduler para la plataforma: cada 5 minutos lee la tabla `entity_config`,
identifica las entidades cuyo `cron_expression` cae dentro de la ventana actual
y dispara una corrida del DAG asociado pasando `entity` (y `mode`,
`cartridge_id`) por `conf`.

Permite que múltiples entidades compartan el mismo `dag_id` pero tengan su
propia cadencia, sin tener que duplicar archivos DAG ni que Airflow conozca
la cadencia por entidad.

Convención para los DAGs base que quieran ser disparados por aquí:
- `schedule=None` en el constructor del DAG (lo programa este scheduler).
- Aceptar `conf.entity` (obligatorio) y opcionalmente `conf.mode`,
  `conf.cartridge_id` desde `context["dag_run"].conf`.

Idempotencia: tras disparar, se hace UPDATE entity_config SET
last_scheduled_at=<fire_time>. La siguiente corrida del scheduler ignora
entidades cuyo fire_time ya esté <= last_scheduled_at.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import requests
from airflow import DAG
from airflow.operators.python import PythonOperator


# ── Config ───────────────────────────────────────────────────────────────────

CARTRIDGE_ID  = "platform"
ENTITY        = "EntityScheduler"
AIRFLOW_URL   = os.environ.get("AIRFLOW_URL", "http://airflow:8080")
# The container exposes AIRFLOW_ADMIN_USER/PASSWORD (set by docker-compose);
# AIRFLOW_USER/PASSWORD are kept as fallback for legacy installs.
AIRFLOW_USER  = (os.environ.get("AIRFLOW_USER")
                 or os.environ.get("AIRFLOW_ADMIN_USER") or "admin")
AIRFLOW_PASS  = (os.environ.get("AIRFLOW_PASSWORD")
                 or os.environ.get("AIRFLOW_ADMIN_PASSWORD") or "admin")
MCP_INFRA_URL = os.environ.get("MCP_INFRA_URL", "http://mcp-infra:8010")

POSTGRES_DSN  = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres:postgres@postgres:5432/modecissions",
).replace("postgresql+psycopg2://", "postgresql://")

INTERVAL_MIN  = 5


default_args = {
    "owner": "platform",
    "retries": 1,
    "retry_delay": timedelta(minutes=1),
}


dag = DAG(
    dag_id="entity_scheduler",
    default_args=default_args,
    description="Meta-scheduler: lee entity_config y dispara DAGs base por entidad según cron",
    schedule_interval=f"*/{INTERVAL_MIN} * * * *",
    start_date=datetime(2026, 5, 1),
    tags=["platform", "scheduler"],
    catchup=False,
    max_active_runs=1,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _pg():
    import psycopg2
    return psycopg2.connect(POSTGRES_DSN)


def _mcp_headers() -> dict[str, str]:
    key = (
        os.environ.get("INTERNAL_API_KEY_AIRFLOW_TO_MCP_INFRA")
        or os.environ.get("INTERNAL_API_KEY")
        or ""
    )
    return {"X-Internal-Service": "airflow", "X-API-Key": key}


def _fire_in_window(cron_expr: str, window_start: datetime, window_end: datetime):
    """Si el cron dispara en [window_start, window_end), devuelve el fire_time
    (UTC, tz-aware). Si no, devuelve None."""
    try:
        from croniter import croniter
    except Exception:
        return None
    base = window_start - timedelta(seconds=1)
    try:
        itr = croniter(cron_expr, base.replace(tzinfo=None))
    except Exception:
        return None
    nxt = itr.get_next(datetime)
    nxt = nxt.replace(tzinfo=timezone.utc)
    return nxt if window_start <= nxt < window_end else None


# ── Task 1 · find_due_entities ───────────────────────────────────────────────

def find_due_entities(**context):
    logical_date = context["logical_date"]
    window_end   = logical_date + timedelta(minutes=INTERVAL_MIN)

    print(f"[entity_scheduler] window UTC [{logical_date.isoformat()}, {window_end.isoformat()})")

    conn = _pg()
    try:
        with conn.cursor() as cur:
            # Fallback de dag_params: si la entidad no tiene override propio,
            # toma el dag_params_example registrado en cartridge_dags para el
            # DAG correspondiente. Así una entidad nueva con cron y DAG
            # asignado funciona sin tener que duplicar el ejemplo en cada row.
            cur.execute(
                """SELECT ec.cartridge_id, ec.entity, ec.dag_id, ec.mode,
                          ec.cron_expression, ec.last_scheduled_at,
                          CASE WHEN ec.dag_params IS NULL
                                 OR ec.dag_params = '{}'::jsonb
                               THEN COALESCE(cd.dag_params_example, '{}'::jsonb)
                               ELSE ec.dag_params
                          END AS dag_params
                     FROM entity_config ec
                     LEFT JOIN cartridge_dags cd
                            ON cd.dag_id = ec.dag_id
                           AND cd.cartridge_id = COALESCE(
                               (SELECT cd2.cartridge_id
                                  FROM cartridge_dags cd2
                                 WHERE cd2.dag_id = ec.dag_id
                                   AND cd2.cartridge_id IN (ec.cartridge_id, 'platform')
                                 ORDER BY CASE WHEN cd2.cartridge_id = ec.cartridge_id THEN 0 ELSE 1 END
                                 LIMIT 1),
                               ec.cartridge_id
                           )
                    WHERE ec.enabled = TRUE
                      AND ec.trigger_type = 'scheduled'
                      AND ec.cron_expression IS NOT NULL
                      AND ec.dag_id IS NOT NULL"""
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    due = []
    for cartridge_id, entity, dag_id, mode, cron_expr, last_at, dag_params in rows:
        fire = _fire_in_window(cron_expr, logical_date, window_end)
        if fire is None:
            continue
        if last_at is not None and last_at >= fire:
            print(f"[entity_scheduler] skip {cartridge_id}.{entity} — last_scheduled_at={last_at} >= fire={fire}")
            continue
        if isinstance(dag_params, str):
            import json as _json
            try:    dag_params = _json.loads(dag_params)
            except: dag_params = {}
        due.append({
            "cartridge_id": cartridge_id,
            "entity":       entity,
            "dag_id":       dag_id,
            "mode":         mode or "full",
            "fire_time":    fire.isoformat(),
            "dag_params":   dag_params or {},
        })

    print(f"[entity_scheduler] {len(due)} due: {[(d['cartridge_id'], d['entity']) for d in due]}")
    context["ti"].xcom_push(key="due", value=due)
    return len(due)


# ── Task 2 · trigger_each ────────────────────────────────────────────────────

def trigger_each(**context):
    due = context["ti"].xcom_pull(task_ids="find_due_entities", key="due") or []
    if not due:
        return {"triggered": 0}

    results = []
    for it in due:
        # Stable dag_run_id per (entity, fire_time) so a retry of this task
        # doesn't create a duplicate run.
        ts_id = it["fire_time"].replace(":", "").replace("-", "").replace("+", "_")
        run_id = f"sched_{it['cartridge_id']}_{it['entity']}_{ts_id}"
        url = f"{AIRFLOW_URL}/api/v1/dags/{it['dag_id']}/dagRuns"
        try:
            requests.patch(
                f"{AIRFLOW_URL}/api/v1/dags/{it['dag_id']}",
                auth=(AIRFLOW_USER, AIRFLOW_PASS),
                json={"is_paused": False},
                timeout=30,
            )
            conf = {
                "entity":       it["entity"],
                "mode":         it["mode"],
                "cartridge_id": it["cartridge_id"],
                "triggered_by": "entity_scheduler",
            }
            # Merge per-entity dag_params (file_pattern, parser, etc.) — but
            # never let them clobber the canonical fields above.
            for k, v in (it.get("dag_params") or {}).items():
                conf.setdefault(k, v)
            r = requests.post(
                url,
                auth=(AIRFLOW_USER, AIRFLOW_PASS),
                json={"dag_run_id": run_id, "conf": conf},
                timeout=30,
            )
            ok = r.status_code in (200, 201, 409)  # 409 = already exists, count as ok (idempotent)
            results.append({
                "cartridge_id": it["cartridge_id"], "entity": it["entity"],
                "dag_id": it["dag_id"], "status": r.status_code,
                "ok": ok, "fire_time": it["fire_time"],
            })
            if ok:
                # Mark scheduled so the next window doesn't re-trigger
                conn = _pg()
                try:
                    with conn.cursor() as cur:
                        cur.execute(
                            "UPDATE entity_config SET last_scheduled_at = %s::timestamptz "
                            "WHERE cartridge_id = %s AND entity = %s",
                            (it["fire_time"], it["cartridge_id"], it["entity"]),
                        )
                    conn.commit()
                finally:
                    conn.close()
        except Exception as exc:                                   # noqa: BLE001
            results.append({
                "cartridge_id": it["cartridge_id"], "entity": it["entity"],
                "status": "exception", "error": str(exc),
            })

    ok_count = sum(1 for r in results if r.get("ok"))
    print(f"[entity_scheduler] triggered {ok_count}/{len(results)}: {results}")
    return {"triggered": ok_count, "results": results}


# ── Task 3 · record_run ──────────────────────────────────────────────────────

def record_run(**context):
    inv = context["ti"].xcom_pull(task_ids="trigger_each") or {}
    started = context["logical_date"].isoformat()
    ended   = datetime.now(timezone.utc).isoformat()
    triggered = int(inv.get("triggered") or 0)
    results = inv.get("results") or []
    total = len(results)
    status = "success" if total == triggered else "partial" if triggered else "failed"
    if total == 0:
        status = "success"
    payload = {
        "tool": "pipeline_run_save",
        "args": {
            "dag_id":       "entity_scheduler",
            "cartridge_id": CARTRIDGE_ID,
            "entity":       ENTITY,
            "run_id":       f"entity_scheduler:{context['run_id']}",
            "airflow_dag_run_id": context["run_id"],
            "mode":         "scheduled",
            "status":       status,
            "started_at":   started,
            "finished_at":  ended,
            "extra":        inv,
        },
    }
    try:
        r = requests.post(
            f"{MCP_INFRA_URL}/mcp/invoke",
            headers=_mcp_headers(),
            json=payload,
            timeout=15,
        )
        print(f"[entity_scheduler] pipeline_run_save → {r.status_code}: {r.text[:200]}")
        r.raise_for_status()
        body = r.json()
        if body.get("error"):
            raise RuntimeError(str(body["error"]))
    except Exception as exc:                                       # noqa: BLE001
        print(f"[entity_scheduler] pipeline_run_save failed: {exc}")
        raise


# ── DAG wiring ───────────────────────────────────────────────────────────────

t_find = PythonOperator(task_id="find_due_entities", python_callable=find_due_entities, dag=dag)
t_trig = PythonOperator(task_id="trigger_each",      python_callable=trigger_each,      dag=dag)
t_rec  = PythonOperator(task_id="record_run",        python_callable=record_run,        dag=dag)

t_find >> t_trig >> t_rec
