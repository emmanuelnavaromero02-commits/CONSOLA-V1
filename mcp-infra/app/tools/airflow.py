"""
Airflow MCP tools — wraps Airflow REST API v1.
Handles DAG management, triggers, status, logs, variables and dynamic DAG creation.
"""
from __future__ import annotations

import re
from pathlib import Path

import httpx

from app.config import settings
from app.registry import tool

_AUTH = (settings.airflow_user, settings.airflow_password)
_BASE = settings.airflow_url.rstrip("/")
_DAG_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,127}$")

# ── Helpers ────────────────────────────────────────────────────────────────────

def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(auth=_AUTH, timeout=30)


def _validate_dag_id(dag_id: str) -> str:
    dag_id = (dag_id or "").strip()
    if not _DAG_ID_RE.fullmatch(dag_id):
        raise ValueError("Invalid dag_id: use letters, numbers and underscores only, starting with a letter")
    return dag_id


def _dag_file_path(dag_id: str) -> Path:
    dag_id = _validate_dag_id(dag_id)
    base = Path(settings.airflow_dags_path).resolve()
    path = (base / f"{dag_id}.py").resolve()
    if path.parent != base:
        raise ValueError("Invalid dag_id path")
    return path


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
        },
        "required": ["dag_id"],
    },
)
async def airflow_trigger_dag(dag_id: str, conf: dict | None = None) -> dict:
    dag_id = _validate_dag_id(dag_id)
    async with _client() as c:
        r = await c.post(
            f"{_BASE}/api/v1/dags/{dag_id}/dagRuns",
            json={"conf": conf or {}},
        )
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
    async with httpx.AsyncClient(auth=_AUTH, timeout=60) as c:
        r = await c.get(
            f"{_BASE}/api/v1/dags/{dag_id}/dagRuns/{dag_run_id}"
            f"/taskInstances/{task_id}/logs/1",
            headers={"Accept": "text/plain"},
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
        },
        "required": ["dag_id", "code"],
    },
)
async def airflow_create_dag(dag_id: str, code: str,
                              cartridge_id: str | None = None,
                              description: str | None = None) -> dict:
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

    # Auto-register in cartridge_dags and save source if cartridge_id provided
    if cartridge_id:
        try:
            import psycopg2
            from app.config import settings as s
            conn = psycopg2.connect(
                host=s.pg_host, port=s.pg_port, dbname=s.pg_db,
                user=s.pg_user, password=s.pg_password,
            )
            with conn.cursor() as cur:
                # Solo registra si el cartucho ya existe — nunca crea cartuchos nuevos
                cur.execute("SELECT 1 FROM cartridges WHERE id = %s", (cartridge_id,))
                if cur.fetchone():
                    cur.execute(
                        """INSERT INTO cartridge_dags
                               (cartridge_id, dag_id, file, description, source_code, updated_at)
                           VALUES (%s, %s, %s, %s, %s, NOW())
                           ON CONFLICT (cartridge_id, dag_id) DO UPDATE
                           SET file        = EXCLUDED.file,
                               description = COALESCE(EXCLUDED.description, cartridge_dags.description),
                               source_code = EXCLUDED.source_code,
                               updated_at  = NOW()""",
                        (cartridge_id, dag_id, f"{dag_id}.py", description, code),
                    )
            conn.commit()
            conn.close()
        except Exception:
            pass  # DAG file is already written; DB registration is best-effort

    return {"created": str(path), "dag_id": dag_id, "bytes": len(code.encode()),
            "registered": bool(cartridge_id)}


@tool(
    name="airflow_delete_dag",
    description="Delete a DAG file from the DAGs directory and remove it from Airflow.",
    input_schema={
        "type": "object",
        "properties": {
            "dag_id": {"type": "string"},
        },
        "required": ["dag_id"],
    },
)
async def airflow_delete_dag(dag_id: str) -> dict:
    dag_id = _validate_dag_id(dag_id)
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
            cur.execute("DELETE FROM cartridge_dags WHERE dag_id = %s", (dag_id,))
        conn.commit()
        conn.close()
    except Exception:
        pass
    return {"dag_id": dag_id, "deleted_file": deleted_file, "deleted_db": deleted_db}


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
