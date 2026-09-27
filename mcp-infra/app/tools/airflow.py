from __future__ import annotations

import asyncio
import re
import os
from datetime import datetime, timezone
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


def _client() -> httpx.AsyncClient:
    headers = _request_headers()
    return httpx.AsyncClient(auth=_AUTH, timeout=30, headers=headers or None)


async def _request_with_transport_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    **kwargs,
) -> httpx.Response:
    for attempt in range(3):
        try:
            return await client.request(method, url, **kwargs)
        except httpx.TransportError:
            if attempt == 2:
                raise
            await asyncio.sleep(0.25 * (attempt + 1))
    raise RuntimeError("unreachable")


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
    return os.environ.get("APP_ENV", "production").lower() in {"development", "dev", "local", "test"}


def _rce_tools_explicitly_enabled() -> bool:
    return os.environ.get("ALLOW_RCE_TOOLS", "").strip().lower() in {
        "true", "1", "yes", "on",
    }


_MANUAL_TIMETABLE_DESCRIPTION = "Never, external triggers only"
_DESCRIBE_RUN_LIMIT = 100
_RUN_SCOPE_CONF_KEYS = ("tenant_id", "workspace_id", "cartridge_id", "mode", "target")
_MARKABLE_RUN_STATES = {"queued", "running"}


def _schedule_kind(dag: dict) -> str:
    """Only an external-trigger-only timetable is manual; anything else is scheduled."""
    if dag.get("schedule_interval") is not None:
        return "scheduled"
    if dag.get("timetable_summary") not in (None, "None"):
        return "scheduled"
    description = dag.get("timetable_description")
    if isinstance(description, str) and description.strip() == _MANUAL_TIMETABLE_DESCRIPTION:
        return "manual"
    return "scheduled"


def _parse_airflow_time(value) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _run_scope_conf(conf) -> dict:
    conf = conf if isinstance(conf, dict) else {}
    return {key: conf[key] for key in _RUN_SCOPE_CONF_KEYS if conf.get(key) is not None}


@tool(
    name="airflow_list_dags",
    description="List all DAGs registered in Airflow with their status.",
    input_schema={"type": "object", "properties": {}, "required": []},
)
async def airflow_list_dags() -> dict:
    async with _client() as c:
        r = await _request_with_transport_retry(c, "GET", f"{_BASE}/api/v1/dags")
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
                "schedule_kind": _schedule_kind(d),
                "timetable_description": d.get("timetable_description"),
            }
            for d in dags
        ]
    }


@tool(
    name="airflow_describe_dag",
    description=(
        "Describe one DAG: pause state, schedule kind, scheduler health and its "
        "queued/running runs."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "dag_id": {"type": "string"},
            "stale_after_seconds": {"type": "integer", "default": 900},
        },
        "required": ["dag_id"],
    },
)
async def airflow_describe_dag(dag_id: str, stale_after_seconds: int = 900) -> dict:
    dag_id = _validate_dag_id(dag_id)
    stale_after = max(60, min(int(stale_after_seconds or 900), 7 * 24 * 3600))
    async with _client() as c:
        r = await _request_with_transport_retry(c, "GET", f"{_BASE}/api/v1/dags/{dag_id}")
        if r.status_code == 404:
            return {"dag_id": dag_id, "found": False, "runs": [], "foreign": {
                "queued": 0, "running": 0, "stale_queued": 0, "stale_running": 0}}
        r.raise_for_status()
        dag = r.json()
        scheduler_healthy = None
        try:
            health = await _request_with_transport_retry(c, "GET", f"{_BASE}/api/v1/health")
            if health.status_code == 200:
                status = (health.json().get("scheduler") or {}).get("status")
                scheduler_healthy = str(status or "").lower() == "healthy"
        except (httpx.HTTPError, ValueError):
            scheduler_healthy = None
        # One page per state, oldest first: the stale backlog and the running
        # runs that block a queue can never be paged out by newer queued runs.
        pages = {}
        for state in ("running", "queued"):
            page = await _request_with_transport_retry(
                c,
                "GET",
                f"{_BASE}/api/v1/dags/{dag_id}/dagRuns",
                params=[
                    ("state", state),
                    ("limit", str(_DESCRIBE_RUN_LIMIT)),
                    ("order_by", "execution_date"),
                ],
            )
            page.raise_for_status()
            pages[state] = page.json()
    now = datetime.now(timezone.utc)
    runs = []
    truncated = {}
    for state, payload in pages.items():
        listed = [run for run in payload.get("dag_runs", []) or [] if isinstance(run, dict)]
        truncated[state] = int(payload.get("total_entries") or len(listed)) > len(listed)
        for run in listed:
            # Airflow 2.x run payloads carry no queued_at; a manually triggered
            # run's logical date is its trigger time.
            queued_since = (
                run.get("queued_at") or run.get("logical_date") or run.get("execution_date")
            )
            queued_at = _parse_airflow_time(queued_since)
            runs.append(
                {
                    "dag_run_id": run.get("dag_run_id"),
                    "state": str(run.get("state") or state).lower(),
                    "queued_at": queued_since,
                    "start_date": run.get("start_date"),
                    "stale": bool(
                        queued_at is not None
                        and (now - queued_at).total_seconds() >= stale_after
                    ),
                    "conf": _run_scope_conf(run.get("conf")),
                }
            )
    return {
        "dag_id": dag_id,
        "found": True,
        "is_paused": bool(dag.get("is_paused")),
        "is_active": bool(dag.get("is_active", True)),
        "has_import_errors": bool(dag.get("has_import_errors")),
        "schedule_kind": _schedule_kind(dag),
        "timetable_description": dag.get("timetable_description"),
        "max_active_runs": dag.get("max_active_runs"),
        "scheduler_healthy": scheduler_healthy,
        "runs": runs,
        "running_truncated": truncated.get("running", False),
        "queued_truncated": truncated.get("queued", False),
        "runs_truncated": any(truncated.values()),
        "foreign": {
            "queued": 0,
            "running": 0,
            "stale_queued": 0,
            "stale_running": 0,
        },
    }


@tool(
    name="airflow_unpause_manual_dag",
    description="Resume a paused manual (schedule=None) DAG so a triggered run can start.",
    input_schema={
        "type": "object",
        "properties": {"dag_id": {"type": "string"}},
        "required": ["dag_id"],
    },
)
async def airflow_unpause_manual_dag(dag_id: str) -> dict:
    dag_id = _validate_dag_id(dag_id)
    async with _client() as c:
        r = await _request_with_transport_retry(c, "GET", f"{_BASE}/api/v1/dags/{dag_id}")
        if r.status_code == 404:
            return {"dag_id": dag_id, "unpaused": False, "was_paused": None, "reason": "dag_not_found"}
        r.raise_for_status()
        dag = r.json()
        if not dag.get("is_active", True) or dag.get("has_import_errors"):
            return {"dag_id": dag_id, "unpaused": False, "was_paused": bool(dag.get("is_paused")),
                    "reason": "dag_inactive"}
        if _schedule_kind(dag) != "manual":
            return {"dag_id": dag_id, "unpaused": False, "was_paused": bool(dag.get("is_paused")),
                    "reason": "not_manual"}
        if not dag.get("is_paused"):
            return {"dag_id": dag_id, "unpaused": False, "was_paused": False, "reason": "already_unpaused"}
        patched = await _request_with_transport_retry(
            c,
            "PATCH",
            f"{_BASE}/api/v1/dags/{dag_id}",
            params={"update_mask": "is_paused"},
            json={"is_paused": False},
        )
        patched.raise_for_status()
        still_paused = bool(patched.json().get("is_paused"))
    return {
        "dag_id": dag_id,
        "unpaused": not still_paused,
        "was_paused": True,
        "reason": "unpaused" if not still_paused else "unpause_rejected",
    }


@tool(
    name="airflow_mark_dag_run_failed",
    description="Mark one queued/running DAG run as failed when its state still matches.",
    input_schema={
        "type": "object",
        "properties": {
            "dag_id": {"type": "string"},
            "dag_run_id": {"type": "string"},
            "expected_states": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["dag_id", "dag_run_id", "expected_states"],
    },
)
async def airflow_mark_dag_run_failed(
    dag_id: str, dag_run_id: str, expected_states: list[str]
) -> dict:
    dag_id = _validate_dag_id(dag_id)
    dag_run_id = _validate_dag_run_id(dag_run_id) or dag_run_id
    expected = {str(state or "").strip().lower() for state in (expected_states or [])}
    if not expected or not expected.issubset(_MARKABLE_RUN_STATES):
        raise ValueError("expected_states must be queued and/or running")
    url = f"{_BASE}/api/v1/dags/{dag_id}/dagRuns/{dag_run_id}"
    async with _client() as c:
        current = await _request_with_transport_retry(c, "GET", url)
        if current.status_code == 404:
            return {"dag_id": dag_id, "dag_run_id": dag_run_id, "marked": False,
                    "found": False, "state": "not_found", "reason": "not_found"}
        current.raise_for_status()
        state = str(current.json().get("state") or "").lower()
        if state not in expected:
            return {"dag_id": dag_id, "dag_run_id": dag_run_id, "marked": False,
                    "found": True, "state": state, "reason": "state_changed"}
        patched = await _request_with_transport_retry(c, "PATCH", url, json={"state": "failed"})
        patched.raise_for_status()
        new_state = str(patched.json().get("state") or "failed").lower()
    return {
        "dag_id": dag_id,
        "dag_run_id": dag_run_id,
        "marked": new_state == "failed",
        "found": True,
        "previous_state": state,
        "state": new_state,
        "reason": "marked_failed" if new_state == "failed" else "state_changed",
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
        r = await _request_with_transport_retry(
            c,
            "POST",
            f"{_BASE}/api/v1/dags/{dag_id}/dagRuns",
            json=payload,
        )
        if r.status_code == 409 and dag_run_id:
            existing = await _request_with_transport_retry(
                c, "GET", f"{_BASE}/api/v1/dags/{dag_id}/dagRuns/{dag_run_id}"
            )
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
    dag_run_id = _validate_dag_run_id(dag_run_id) or dag_run_id
    async with _client() as c:
        r = await _request_with_transport_retry(
            c, "GET", f"{_BASE}/api/v1/dags/{dag_id}/dagRuns/{dag_run_id}"
        )
        if r.status_code == 404:
            return {
                "dag_id": dag_id,
                "dag_run_id": dag_run_id,
                "found": False,
                "state": "not_found",
                "start_date": None,
                "end_date": None,
            }
        r.raise_for_status()
        data = r.json()
    return {
        "dag_id":     dag_id,
        "dag_run_id": data.get("dag_run_id", dag_run_id),
        "found":      True,
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
    dag_run_id = _validate_dag_run_id(dag_run_id) or dag_run_id
    async with httpx.AsyncClient(auth=_AUTH, timeout=60, headers=_request_headers()) as c:
        r = await _request_with_transport_retry(
            c,
            "GET",
            f"{_BASE}/api/v1/dags/{dag_id}/dagRuns/{dag_run_id}"
            f"/taskInstances/{task_id}/logs/1",
            headers=_request_headers({"Accept": "text/plain"}),
        )
        if r.status_code == 404:
            return {
                "logs": "",
                "dag_id": dag_id,
                "dag_run_id": dag_run_id,
                "task_id": task_id,
                "found": False,
            }
        r.raise_for_status()
    return {
        "logs": r.text[-6000:],
        "dag_id": dag_id,
        "dag_run_id": dag_run_id,
        "task_id": task_id,
        "found": True,
    }


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
    try:
        async with _client() as c:
            r = await _request_with_transport_retry(c, "DELETE", f"{_BASE}/api/v1/dags/{dag_id}")
            deleted_db = r.status_code in (200, 204)
    except Exception:
        deleted_db = False
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
    if not _rce_tools_explicitly_enabled():
        raise PermissionError(
            "airflow_set_variable refuses to run without explicit "
            "ALLOW_RCE_TOOLS=true. APP_ENV=development is not enough; the "
            "second gate must be set by the local/E2E harness."
        )
    async with _client() as c:
        r = await _request_with_transport_retry(
            c,
            "POST",
            f"{_BASE}/api/v1/variables",
            json={"key": key, "value": value},
        )
        if r.status_code == 409:
            r = await _request_with_transport_retry(
                c,
                "PATCH",
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
    dag_run_id = _validate_dag_run_id(dag_run_id) or dag_run_id
    async with _client() as c:
        r = await _request_with_transport_retry(
            c,
            "GET",
            f"{_BASE}/api/v1/dags/{dag_id}/dagRuns/{dag_run_id}/taskInstances"
        )
        if r.status_code == 404:
            return {
                "dag_id": dag_id,
                "dag_run_id": dag_run_id,
                "found": False,
                "tasks": [],
            }
        r.raise_for_status()
        tasks = r.json().get("task_instances", [])
    return {
        "dag_id": dag_id,
        "dag_run_id": dag_run_id,
        "found": True,
        "tasks": [
            {
                "task_id":  t["task_id"],
                "state":    t["state"],
                "duration": t.get("duration"),
                "start_date": t.get("start_date"),
                "end_date": t.get("end_date"),
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
        r = await _request_with_transport_retry(
            c,
            "GET",
            f"{_BASE}/api/v1/dags/{dag_id}/dagRuns",
            params={"limit": limit, "order_by": "-start_date"},
        )
        if r.status_code == 404:
            return {
                "dag_id": dag_id,
                "found": False,
                "runs": [],
            }
        r.raise_for_status()
        runs = r.json().get("dag_runs", [])
    return {
        "dag_id": dag_id,
        "found": True,
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
