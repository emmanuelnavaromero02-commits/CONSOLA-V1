"""
Airflow MCP tools — wraps Airflow REST API v1.
Handles DAG management, triggers, status, logs, variables and dynamic DAG creation.
"""
from __future__ import annotations

import re
import os
from pathlib import Path

import httpx

from app.config import settings
from app.middleware.request_id import request_id_var
from app.registry import tool

_AUTH = (settings.airflow_user, settings.airflow_password)
_BASE = settings.airflow_url.rstrip("/")
_DAG_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,127}$")
_DAG_RUN_ID_RE = re.compile(r"^[A-Za-z0-9_.:+-]{1,250}$")
_CARTRIDGE_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,127}$")

# ── Helpers ────────────────────────────────────────────────────────────────────

def _client() -> httpx.AsyncClient:
    headers = _request_headers()
    return httpx.AsyncClient(auth=_AUTH, timeout=30, headers=headers or None)


def _request_headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    headers = dict(extra or {})
    rid = request_id_var.get()
    if rid:
        headers["X-Request-ID"] = rid
    return headers


def _validate_dag_id(dag_id: str) -> str:
    dag_id = (dag_id or "").strip()
    if not _DAG_ID_RE.fullmatch(dag_id):
        raise ValueError("Invalid dag_id: use letters, numbers and underscores only, starting with a letter")
    return dag_id


def _validate_dag_run_id(dag_run_id: str | None) -> str | None:
    if dag_run_id is None:
        return None
    dag_run_id = dag_run_id.strip()
    if not _DAG_RUN_ID_RE.fullmatch(dag_run_id):
        raise ValueError("Invalid dag_run_id")
    return dag_run_id


def _validate_cartridge_id(cartridge_id: str) -> str:
    cartridge_id = (cartridge_id or "").strip()
    if not _CARTRIDGE_ID_RE.fullmatch(cartridge_id):
        raise ValueError("Invalid cartridge_id: use letters, numbers and underscores only, starting with a letter")
    return cartridge_id


def _dag_file_path(dag_id: str) -> Path:
    dag_id = _validate_dag_id(dag_id)
    base = Path(settings.airflow_dags_path).resolve()
    path = (base / f"{dag_id}.py").resolve()
    if path.parent != base:
        raise ValueError("Invalid dag_id path")
    return path


def _is_development() -> bool:
    # v1.43.2 (Codex P1-2): default ``production`` — a forgotten
    # APP_ENV no longer enables airflow_create_dag (RCE-shaped tool)
    # on a fresh deploy.
    return os.environ.get("APP_ENV", "production").lower() in {"development", "dev", "local", "test"}


def _rce_tools_explicitly_enabled() -> bool:
    """v1.43.4 (Codex H1): second gate. APP_ENV=development was used
    to enable airflow_create_dag in dev environments — and Codex
    proved that an operator who flips APP_ENV (e.g. to debug a
    production-only path) implicitly unlocks the RCE tool. Require
    an explicit second opt-in so APP_ENV alone is no longer enough.
    Default off; only ``ALLOW_RCE_TOOLS=true`` (case-insensitive)
    flips the gate.
    """
    return os.environ.get("ALLOW_RCE_TOOLS", "").strip().lower() in {
        "true", "1", "yes", "on",
    }


# ── Tools ──────────────────────────────────────────────────────────────────────

@tool(
    name="airflow_list_dags",
    description="List all DAGs registered in Airflow with their status.",
    input_schema={"type": "object", "properties": {}, "required": []},
)
async def airflow_list_dags() -> dict:
    async with _client() as c:
        r = await c.get(f"{_BASE}/api/v1/dags")
        r.raise_for_status()
        dags = r.json().get("dags", [])
    return {
        "dags": [
            {
                "dag_id":    d["dag_id"],
                "is_paused": d["is_paused"],
                "is_active": d.get("is_active", True),
                "tags":      [t["name"] for t in d.get("tags", [])],
                "description": d.get("description", ""),
            }
            for d in dags
        ]
    }


@tool(
    name="airflow_trigger_dag",
    description="Trigger a DAG run. Returns dag_run_id to track status.",
    input_schema={
        "type": "object",
        "properties": {
            "dag_id": {"type": "string", "description": "DAG ID to trigger"},
            "conf":   {"type": "object", "description": "Optional run configuration dict"},
            "dag_run_id": {
                "type": "string",
                "description": "Optional idempotency key for the Airflow DAG run.",
            },
        },
        "required": ["dag_id"],
    },
)
async def airflow_trigger_dag(
    dag_id: str,
    conf: dict | None = None,
    dag_run_id: str | None = None,
) -> dict:
    dag_id = _validate_dag_id(dag_id)
    dag_run_id = _validate_dag_run_id(dag_run_id)
    payload = {"conf": conf or {}}
    if dag_run_id:
        payload["dag_run_id"] = dag_run_id
    async with _client() as c:
        r = await c.post(
            f"{_BASE}/api/v1/dags/{dag_id}/dagRuns",
            json=payload,
        )
        if r.status_code == 409 and dag_run_id:
            existing = await c.get(f"{_BASE}/api/v1/dags/{dag_id}/dagRuns/{dag_run_id}")
            existing.raise_for_status()
            data = existing.json()
            return {
                "dag_id": dag_id,
                "dag_run_id": data["dag_run_id"],
                "state": data["state"],
                "start_date": data.get("start_date"),
                "idempotent_replay": True,
            }
        r.raise_for_status()
        data = r.json()
    return {
        "dag_id":     dag_id,
        "dag_run_id": data["dag_run_id"],
        "state":      data["state"],
        "start_date": data.get("start_date"),
    }


@tool(
    name="airflow_get_run_status",
    description="Get the current state of a DAG run (queued/running/success/failed).",
    input_schema={
        "type": "object",
        "properties": {
            "dag_id":     {"type": "string"},
            "dag_run_id": {"type": "string"},
        },
        "required": ["dag_id", "dag_run_id"],
    },
)
async def airflow_get_run_status(dag_id: str, dag_run_id: str) -> dict:
    dag_id = _validate_dag_id(dag_id)
    async with _client() as c:
        r = await c.get(f"{_BASE}/api/v1/dags/{dag_id}/dagRuns/{dag_run_id}")
        r.raise_for_status()
        data = r.json()
    return {
        "state":      data["state"],
        "start_date": data.get("start_date"),
        "end_date":   data.get("end_date"),
    }


@tool(
    name="airflow_get_task_logs",
    description="Get the execution logs of a specific task in a DAG run.",
    input_schema={
        "type": "object",
        "properties": {
            "dag_id":     {"type": "string"},
            "dag_run_id": {"type": "string"},
            "task_id":    {"type": "string"},
        },
        "required": ["dag_id", "dag_run_id", "task_id"],
    },
)
async def airflow_get_task_logs(dag_id: str, dag_run_id: str, task_id: str) -> dict:
    dag_id = _validate_dag_id(dag_id)
    async with httpx.AsyncClient(auth=_AUTH, timeout=60, headers=_request_headers()) as c:
        r = await c.get(
            f"{_BASE}/api/v1/dags/{dag_id}/dagRuns/{dag_run_id}"
            f"/taskInstances/{task_id}/logs/1",
            headers=_request_headers({"Accept": "text/plain"}),
        )
        r.raise_for_status()
    # Trim to last 6 000 chars so it fits in context
    return {"logs": r.text[-6000:], "dag_id": dag_id, "task_id": task_id}


@tool(
    name="airflow_create_dag",
    description=(
        "Write a Python DAG file directly to Airflow's DAGs directory. "
        "Airflow picks it up within seconds automatically. "
        "NAMING CONVENTION: dag_id must be prefixed with the cartridge_id, "
        "e.g. 'replicon_timeentry_full', 'salesforce_opportunities_incremental'. "
        "If cartridge_id is provided, also registers the DAG in cartridge_dags table "
        "and saves the source code so the AI can retrieve it for future modifications."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "dag_id":       {"type": "string",
                             "description": "DAG id — must start with cartridge prefix, e.g. replicon_extract"},
            "code":         {"type": "string", "description": "Complete Python DAG source code"},
            "cartridge_id": {"type": "string",
                             "description": "Cartridge this DAG belongs to (e.g. 'replicon'). "
                                            "Enables auto-registration and source storage."},
            "description":  {"type": "string", "description": "Short description shown in Studio"},
            "dag_params_example": {"type": "object",
                             "description": "Optional default JSON shown as a template in Studio's dag_params editor."},
        },
        "required": ["dag_id", "code"],
    },
)
async def airflow_create_dag(dag_id: str, code: str,
                              cartridge_id: str | None = None,
                              description: str | None = None,
                              dag_params_example: dict | None = None) -> dict:
    # v1.43.4 (Codex H1): double-gate. APP_ENV must be a dev variant
    # AND ALLOW_RCE_TOOLS must be explicitly set. Either gate alone
    # was demonstrably bypassable: APP_ENV gets flipped to debug
    # production-only paths, and a "default-on" RCE tool gated by
    # ALLOW_RCE_TOOLS alone would fail open if the variable is
    # forgotten. Require both.
    if not _is_development():
        raise PermissionError(
            "airflow_create_dag is disabled outside development because writing "
            "Python into the Airflow DAG directory is remote code execution."
        )
    if not _rce_tools_explicitly_enabled():
        raise PermissionError(
            "airflow_create_dag refuses to run without explicit "
            "ALLOW_RCE_TOOLS=true. APP_ENV=development is not enough; the "
            "second opt-in protects against debugging-time RCE."
        )
    dag_id = _validate_dag_id(dag_id)
    path = _dag_file_path(dag_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Fuerza is_paused_upon_creation=False para que el DAG arranque activo
    import re
    code = re.sub(
        r'(is_paused_upon_creation\s*=\s*(?:True|False),?\s*\n?)',
        '',
        code,
    )
    code = re.sub(
        r'(@dag\s*\()',
        '@dag(\n    is_paused_upon_creation=False,',
        code,
        count=1,
    )
    path.write_text(code, encoding="utf-8")

    sched_match = re.search(
        r"schedule_interval\s*=\s*("
        r"None|"
        r"\"\"\"([^\"]*)\"\"\"|"
        r"'''([^']*)'''|"
        r"\"([^\"]*)\"|"
        r"'([^']*)'"
        r")",
        code,
    )
    cron_value: str | None = None
    trigger_type = "manual"
    if sched_match:
        whole = sched_match.group(1)
        if whole == "None":
            trigger_type = "manual"
        else:
            cron_value = next((g for g in sched_match.groups()[1:] if g is not None), None)
            trigger_type = "scheduled" if cron_value else "manual"

    entities_synced = 0
    registered = False

    # Auto-register in cartridge_dags and save source if cartridge_id provided
    try:
        import json as _json
        import psycopg2
        from app.config import settings as s

        conn = psycopg2.connect(
            host=s.pg_host, port=s.pg_port, dbname=s.pg_db,
            user=s.pg_user, password=s.pg_password,
        )
        with conn.cursor() as cur:
            if cartridge_id:
                ex_json = _json.dumps(dag_params_example) if dag_params_example is not None else None
                # Solo registra si el cartucho ya existe — nunca crea cartuchos nuevos
                cur.execute("SELECT 1 FROM cartridges WHERE id = %s", (cartridge_id,))
                if cur.fetchone():
                    cur.execute(
                        """INSERT INTO cartridge_dags
                               (cartridge_id, dag_id, file, description, source_code,
                                dag_params_example, updated_at)
                           VALUES (%s, %s, %s, %s, %s, COALESCE(%s::jsonb, '{}'::jsonb), NOW())
                           ON CONFLICT (cartridge_id, dag_id) DO UPDATE
                           SET file               = EXCLUDED.file,
                               description        = COALESCE(EXCLUDED.description, cartridge_dags.description),
                               source_code        = EXCLUDED.source_code,
                               dag_params_example = CASE
                                   WHEN %s::jsonb IS NULL THEN cartridge_dags.dag_params_example
                                   ELSE EXCLUDED.dag_params_example
                               END,
                               updated_at         = NOW()""",
                        (cartridge_id, dag_id, f"{dag_id}.py", description, code, ex_json, ex_json),
                    )
                    registered = True

            if cron_value is not None:
                cur.execute(
                    """UPDATE entity_config
                          SET trigger_type    = %s,
                              cron_expression = %s
                        WHERE dag_id = %s""",
                    (trigger_type, cron_value, dag_id),
                )
                entities_synced = cur.rowcount
            else:
                cur.execute(
                    """UPDATE entity_config
                          SET trigger_type = CASE
                              WHEN cron_expression IS NOT NULL THEN 'scheduled'
                              ELSE 'manual' END
                        WHERE dag_id = %s""",
                    (dag_id,),
                )
                entities_synced = cur.rowcount
        conn.commit()
        conn.close()
    except Exception:
        # DAG file is already written; DB registration/sync is best-effort.
        pass

    return {"created": str(path), "dag_id": dag_id, "bytes": len(code.encode()),
            "registered": registered,
            "schedule": {"trigger_type": trigger_type, "cron": cron_value,
                         "entities_synced": entities_synced}}


@tool(
    name="airflow_delete_dag",
    description="Delete a DAG file from the DAGs directory and remove it from Airflow.",
    input_schema={
        "type": "object",
        "properties": {
            "dag_id": {"type": "string"},
            "cartridge_id": {"type": "string"},
        },
        "required": ["dag_id"],
    },
)
async def airflow_delete_dag(dag_id: str, cartridge_id: str | None = None) -> dict:
    if not _is_development():
        raise PermissionError(
            "airflow_delete_dag is disabled outside development because "
            "deleting DAGs is a destructive operation."
        )
    if not _rce_tools_explicitly_enabled():
        raise PermissionError(
            "airflow_delete_dag refuses to run without explicit "
            "ALLOW_RCE_TOOLS=true. APP_ENV=development is not enough; the "
            "second opt-in protects destructive Airflow operations."
        )
    dag_id = _validate_dag_id(dag_id)
    safe_cartridge_id = _validate_cartridge_id(cartridge_id) if cartridge_id else None
    if safe_cartridge_id:
        try:
            import psycopg2
            from app.config import settings as s
            conn = psycopg2.connect(
                host=s.pg_host, port=s.pg_port, dbname=s.pg_db,
                user=s.pg_user, password=s.pg_password,
            )
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM cartridge_dags WHERE dag_id = %s AND cartridge_id = %s",
                    (dag_id, safe_cartridge_id),
                )
                owned = cur.fetchone() is not None
            conn.close()
            if not owned:
                raise PermissionError(
                    f"airflow_delete_dag refuses to delete '{dag_id}' because it is not "
                    f"registered for cartridge '{safe_cartridge_id}'."
                )
        except PermissionError:
            raise
        except Exception as exc:
            raise RuntimeError("Could not verify DAG cartridge ownership before delete") from exc
    path = _dag_file_path(dag_id)
    deleted_file = False
    if path.exists():
        path.unlink()
        deleted_file = True
    # Delete from Airflow metadata DB
    try:
        async with _client() as c:
            r = await c.delete(f"{_BASE}/api/v1/dags/{dag_id}")
            deleted_db = r.status_code in (200, 204)
    except Exception:
        deleted_db = False
    # Delete from cartridge_dags
    try:
        import psycopg2
        from app.config import settings as s
        conn = psycopg2.connect(
            host=s.pg_host, port=s.pg_port, dbname=s.pg_db,
            user=s.pg_user, password=s.pg_password,
        )
        with conn.cursor() as cur:
            if safe_cartridge_id:
                cur.execute(
                    "DELETE FROM cartridge_dags WHERE dag_id = %s AND cartridge_id = %s",
                    (dag_id, safe_cartridge_id),
                )
            else:
                cur.execute("DELETE FROM cartridge_dags WHERE dag_id = %s", (dag_id,))
        conn.commit()
        conn.close()
    except Exception:
        pass
    return {
        "dag_id": dag_id,
        "cartridge_id": safe_cartridge_id,
        "deleted_file": deleted_file,
        "deleted_db": deleted_db,
    }


@tool(
    name="airflow_set_variable",
    description="Create or update an Airflow Variable (key-value store used by DAGs).",
    input_schema={
        "type": "object",
        "properties": {
            "key":   {"type": "string"},
            "value": {"type": "string"},
        },
        "required": ["key", "value"],
    },
)
async def airflow_set_variable(key: str, value: str) -> dict:
    if not _is_development():
        raise PermissionError(
            "airflow_set_variable is disabled outside development because "
            "Airflow Variables are persistent runtime configuration."
        )
    async with _client() as c:
        r = await c.post(
            f"{_BASE}/api/v1/variables",
            json={"key": key, "value": value},
        )
        if r.status_code == 409:          # already exists → patch
            r = await c.patch(
                f"{_BASE}/api/v1/variables/{key}",
                json={"key": key, "value": value},
            )
        r.raise_for_status()
    return {"key": key, "set": True}


@tool(
    name="airflow_list_task_instances",
    description="List task instances for a DAG run to see per-task state.",
    input_schema={
        "type": "object",
        "properties": {
            "dag_id":     {"type": "string"},
            "dag_run_id": {"type": "string"},
        },
        "required": ["dag_id", "dag_run_id"],
    },
)
async def airflow_list_task_instances(dag_id: str, dag_run_id: str) -> dict:
    dag_id = _validate_dag_id(dag_id)
    async with _client() as c:
        r = await c.get(
            f"{_BASE}/api/v1/dags/{dag_id}/dagRuns/{dag_run_id}/taskInstances"
        )
        r.raise_for_status()
        tasks = r.json().get("task_instances", [])
    return {
        "tasks": [
            {
                "task_id":  t["task_id"],
                "state":    t["state"],
                "duration": t.get("duration"),
            }
            for t in tasks
        ]
    }


@tool(
    name="airflow_list_dag_runs",
    description="List recent runs of a DAG ordered by most recent first. Returns dag_run_id needed for airflow_get_task_logs.",
    input_schema={
        "type": "object",
        "properties": {
            "dag_id": {"type": "string"},
            "limit":  {"type": "integer", "default": 10},
        },
        "required": ["dag_id"],
    },
)
async def airflow_list_dag_runs(dag_id: str, limit: int = 10) -> dict:
    dag_id = _validate_dag_id(dag_id)
    async with _client() as c:
        r = await c.get(
            f"{_BASE}/api/v1/dags/{dag_id}/dagRuns",
            params={"limit": limit, "order_by": "-start_date"},
        )
        r.raise_for_status()
        runs = r.json().get("dag_runs", [])
    return {
        "dag_id": dag_id,
        "runs": [
            {
                "dag_run_id": run["dag_run_id"],
                "state":      run["state"],
                "start_date": run.get("start_date"),
                "end_date":   run.get("end_date"),
                "conf":       run.get("conf", {}),
            }
            for run in runs
        ],
    }
