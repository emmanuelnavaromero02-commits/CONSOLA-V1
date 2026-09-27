from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.schemas.pipeline_automations import (
    AUTOMATIONS_SCHEMA_VERSION,
    MAX_AUTOMATIONS,
    Automation,
    AutomationRun,
    AutomationsResponse,
)
from app.services import auth
from app.services.control_room.business_cartridge_scope import (
    allowed_business_cartridges,
)
from app.services.db_scope import run_with_db_scope
from app.services.permission_roles import PLATFORM_ADMIN_ROLES
from app.services.permissions import user_role


PLATFORM_CARTRIDGE = "platform"
READ_ONLY_TRANSACTION_SQL = "SET TRANSACTION READ ONLY"
AIRFLOW_LIST_TIMEOUT_SECONDS = 8.0
RUN_STATUS = re.compile(r"^[a-z_]{1,40}$")
ACTIVE_RUN_STATES = frozenset({"queued", "running", "scheduled", "up_for_retry"})
EXTRACT_DAG = re.compile(r"^[a-z][a-z0-9_]*_extract(_all)?$")
CARTRIDGE_ID = re.compile(r"^[a-z0-9_]{1,120}$")
DAG_ID = re.compile(r"^[A-Za-z0-9_.\-]{1,250}$")
REGISTERED_DAGS_SQL = """
SELECT cartridge_id, dag_id, description, trigger
  FROM cartridge_dags
 WHERE ($1::text[] IS NULL OR cartridge_id = ANY($1::text[]))
 ORDER BY cartridge_id, dag_id
 LIMIT $2
"""
RUN_SUMMARY_CTE = """
WITH runs AS (
    SELECT dag_id,
           COALESCE(airflow_dag_run_id, run_id) AS run_key,
           min(started_at) AS started_at,
           max(finished_at) AS finished_at,
           max(lower(status)) FILTER (WHERE run_id = airflow_dag_run_id)
               AS aggregate_status,
           bool_or(lower(status) = ANY($4::text[])) AS any_active,
           bool_or(lower(status) = ANY($5::text[])) AS any_failed,
           bool_or(lower(status) = ANY($6::text[])) AS any_succeeded,
           bool_and(lower(status) = ANY($6::text[])) AS all_succeeded
      FROM pipeline_runs
     WHERE tenant_id = $1::uuid
       AND workspace_id = $2::uuid
       AND dag_id = ANY($3::text[])
       AND COALESCE(extra ->> 'source', '') <> 'extraction_runs_mirror'
     GROUP BY dag_id, COALESCE(airflow_dag_run_id, run_id)
)
"""
ACTIVE_RUNS_SQL = RUN_SUMMARY_CTE + """
SELECT dag_id, count(*) AS active_runs
  FROM runs
 WHERE COALESCE(aggregate_status = ANY($4::text[]), any_active)
 GROUP BY dag_id
"""
LAST_RUNS_SQL = RUN_SUMMARY_CTE + """
SELECT DISTINCT ON (dag_id)
       dag_id, started_at, finished_at, aggregate_status,
       any_active, any_failed, any_succeeded, all_succeeded
  FROM runs
 ORDER BY dag_id, started_at DESC NULLS LAST, run_key DESC
"""
FAILED_RUN_STATES = frozenset({"failed", "error", "upstream_failed", "cancelled", "removed"})
SUCCEEDED_RUN_STATES = frozenset({"success", "noop", "skipped", "skipped_explicit"})
NOTES = {
    "unreachable": "No se pudo consultar Airflow en este momento; estado desconocido.",
    "missing": "No aparece en Airflow; no se ejecutará hasta que se despliegue.",
    "inactive": "Airflow no tiene cargado su archivo; no se ejecutará.",
    "paused_extract": "Se activa automáticamente al pulsar Extraer",
    "paused_manual": (
        "En pausa; una ejecución iniciada quedará en espera hasta que un operador "
        "de plataforma la reactive."
    ),
    "paused_by_operator": (
        "En pausa por un operador de plataforma; no se ejecutará en su horario"
    ),
    "active_scheduled": "Activa: se ejecuta en su horario.",
    "active_manual": "Activa: se ejecuta cuando alguien la inicia.",
}
_WEEKDAYS = {
    "0": "domingo",
    "7": "domingo",
    "1": "lunes",
    "2": "martes",
    "3": "miércoles",
    "4": "jueves",
    "5": "viernes",
    "6": "sábado",
    "sun": "domingo",
    "mon": "lunes",
    "tue": "martes",
    "wed": "miércoles",
    "thu": "jueves",
    "fri": "viernes",
    "sat": "sábado",
}
_PRESETS = {
    "@hourly": "Cada hora",
    "@daily": "Todos los días a las 00:00 (hora de Airflow)",
    "@midnight": "Todos los días a las 00:00 (hora de Airflow)",
    "@weekly": "Cada domingo a las 00:00 (hora de Airflow)",
    "@monthly": "El día 1 de cada mes a las 00:00 (hora de Airflow)",
    "@once": "Una sola vez",
}

Invoker = Callable[..., Awaitable[Any]]


def _clock(hour: str, minute: str) -> str | None:
    if not (hour.isdigit() and minute.isdigit()):
        return None
    if int(hour) > 23 or int(minute) > 59:
        return None
    return f"{int(hour):02d}:{int(minute):02d}"


def describe_cron(expression: str) -> str | None:
    text = " ".join(str(expression or "").split())
    if not text:
        return None
    if text.lower() in _PRESETS:
        return _PRESETS[text.lower()]
    parts = text.split(" ")
    if len(parts) != 5:
        return f"Programación cron: {text}"[:160]
    minute, hour, day, month, weekday = parts
    step = re.fullmatch(r"\*/([1-9][0-9]?)", minute)
    if step and hour == day == month == weekday == "*":
        return f"Cada {int(step.group(1))} minutos"
    hourly = re.fullmatch(r"\*/([1-9][0-9]?)", hour)
    if minute.isdigit() and hourly and day == month == weekday == "*":
        return f"Cada {int(hourly.group(1))} horas, en el minuto {int(minute)}"
    if minute.isdigit() and hour == day == month == weekday == "*":
        return f"Cada hora, en el minuto {int(minute)}"
    clock = _clock(hour, minute)
    if clock and day == month == "*":
        if weekday == "*":
            return f"Todos los días a las {clock} (hora de Airflow)"
        name = _WEEKDAYS.get(weekday.lower())
        if name:
            return f"Cada {name} a las {clock} (hora de Airflow)"
    return f"Programación cron: {text}"[:160]


def describe_schedule(value: object) -> str | None:
    if isinstance(value, Mapping):
        if "value" in value:
            return describe_schedule(value.get("value"))
        if str(value.get("__type") or "") == "TimeDelta":
            seconds = int(value.get("days") or 0) * 86400 + int(value.get("seconds") or 0)
            if seconds <= 0:
                return None
            if seconds % 86400 == 0:
                return f"Cada {seconds // 86400} días"
            if seconds % 3600 == 0:
                return f"Cada {seconds // 3600} horas"
            if seconds % 60 == 0:
                return f"Cada {seconds // 60} minutos"
        return None
    if isinstance(value, str) and value.strip() and value.strip().lower() != "none":
        return describe_cron(value)
    return None


def _label(row: Mapping[str, Any], dag: Mapping[str, Any] | None) -> str:
    for candidate in (row.get("description"), (dag or {}).get("description")):
        text = " ".join(str(candidate or "").split())
        if text:
            return text[:160]
    return str(row["dag_id"])[:160]


def _kind(row: Mapping[str, Any], dag: Mapping[str, Any] | None) -> str:
    declared = str((dag or {}).get("schedule_kind") or "").strip().lower()
    if declared in {"manual", "scheduled"}:
        return declared
    return "scheduled" if str(row.get("trigger") or "") == "scheduled" else "manual"


def _state(
    dag_id: str, kind: str, dag: Mapping[str, Any] | None, *, airflow_available: bool
) -> tuple[str, str]:
    if not airflow_available:
        return "unavailable", NOTES["unreachable"]
    if dag is None:
        return "unavailable", NOTES["missing"]
    if dag.get("is_active") is False:
        return "unavailable", NOTES["inactive"]
    paused = bool(dag.get("is_paused", dag.get("paused", False)))
    if paused and kind == "scheduled":
        return "paused_by_operator", NOTES["paused_by_operator"]
    if paused:
        note = "paused_extract" if EXTRACT_DAG.fullmatch(dag_id) else "paused_manual"
        return "paused_manual", NOTES[note]
    return "active", NOTES["active_scheduled" if kind == "scheduled" else "active_manual"]


@dataclass(frozen=True)
class RegisteredDags:
    rows: list[dict[str, Any]]
    active: dict[str, int]
    last: dict[str, AutomationRun]


def run_status(row: Mapping[str, Any]) -> str:
    aggregate = str(row.get("aggregate_status") or "").strip().lower()
    if aggregate:
        return aggregate
    if row.get("any_active"):
        return "running"
    if row.get("all_succeeded"):
        return "success"
    if row.get("any_failed") and not row.get("any_succeeded"):
        return "failed"
    return "partial"


def _run(row: Mapping[str, Any]) -> AutomationRun | None:
    status = run_status(row)
    if not RUN_STATUS.fullmatch(status):
        return None
    return AutomationRun(
        status=status,
        started_at=_aware(row.get("started_at")),
        finished_at=_aware(row.get("finished_at")),
    )


def _aware(value: object) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def is_platform_admin(user: Mapping[str, Any]) -> bool:
    return user_role(dict(user)) in PLATFORM_ADMIN_ROLES


def _visible_cartridges(user: Mapping[str, Any]) -> list[str] | None:
    allowed = allowed_business_cartridges(user)
    if allowed is None:
        return None
    visible = set(allowed) - {PLATFORM_CARTRIDGE}
    if is_platform_admin(user):
        visible.add(PLATFORM_CARTRIDGE)
    return sorted(visible)


async def _registered_dags(user: Mapping[str, Any]) -> RegisteredDags:
    cartridges = _visible_cartridges(user)
    if cartridges is not None and not cartridges:
        return RegisteredDags([], {}, {})
    pool = await auth.pool()
    platform = is_platform_admin(user)

    async def _read(conn: Any, tenant: str | None, workspace: str) -> RegisteredDags:
        await conn.execute(READ_ONLY_TRANSACTION_SQL)
        rows = [
            dict(row)
            for row in await conn.fetch(REGISTERED_DAGS_SQL, cartridges, MAX_AUTOMATIONS)
            if DAG_ID.fullmatch(str(row.get("dag_id") or ""))
            and (platform or str(row.get("cartridge_id") or "") != PLATFORM_CARTRIDGE)
        ]
        workspace_dags = sorted(
            {
                str(row["dag_id"])
                for row in rows
                if str(row.get("cartridge_id") or "") != PLATFORM_CARTRIDGE
            }
        )
        if not workspace_dags or not tenant:
            return RegisteredDags(rows, {}, {})
        params = (
            tenant,
            workspace,
            workspace_dags,
            sorted(ACTIVE_RUN_STATES),
            sorted(FAILED_RUN_STATES),
            sorted(SUCCEEDED_RUN_STATES),
        )
        active = {
            str(row["dag_id"]): int(row["active_runs"])
            for row in await conn.fetch(ACTIVE_RUNS_SQL, *params)
        }
        last = {
            str(row["dag_id"]): run
            for row in await conn.fetch(LAST_RUNS_SQL, *params)
            if (run := _run(row)) is not None
        }
        return RegisteredDags(rows, active, last)

    return await run_with_db_scope(pool, dict(user), _read)


async def _default_invoke(tool: str, args: dict[str, Any], user: Mapping[str, Any]) -> Any:
    from app.services import mcp_registry

    return await mcp_registry.invoke("infra", tool, args, user=dict(user))


async def _airflow_dags(
    user: Mapping[str, Any], invoke: Invoker
) -> dict[str, Mapping[str, Any]] | None:
    try:
        result = await asyncio.wait_for(
            invoke("airflow_list_dags", {}, user), timeout=AIRFLOW_LIST_TIMEOUT_SECONDS
        )
    except Exception:
        return None
    if not isinstance(result, Mapping) or result.get("error"):
        return None
    dags = result.get("dags")
    if not isinstance(dags, Sequence):
        return None
    return {
        str(dag.get("dag_id")): dag
        for dag in dags
        if isinstance(dag, Mapping) and str(dag.get("dag_id") or "").strip()
    }


async def list_automations(
    user: Mapping[str, Any], *, invoke: Invoker | None = None
) -> AutomationsResponse:
    call = invoke or _default_invoke
    registered = await _registered_dags(user)
    airflow = await _airflow_dags(user, call)
    available = airflow is not None
    automations: list[Automation] = []
    for row in registered.rows:
        dag_id = str(row["dag_id"])
        dag = (airflow or {}).get(dag_id)
        kind = _kind(row, dag)
        state, note = _state(dag_id, kind, dag, airflow_available=available)
        cartridge = str(row.get("cartridge_id") or "").strip()
        workspace_dag = cartridge != PLATFORM_CARTRIDGE
        automations.append(
            Automation(
                dag_id=dag_id,
                label=_label(row, dag),
                cartridge_id=(
                    cartridge
                    if CARTRIDGE_ID.fullmatch(cartridge) and workspace_dag
                    else None
                ),
                kind=kind,
                schedule_description=(
                    describe_schedule((dag or {}).get("schedule_interval"))
                    if kind == "scheduled"
                    else None
                ),
                state=state,
                state_note_es=note,
                active_runs=registered.active.get(dag_id, 0) if workspace_dag else None,
                last_run=registered.last.get(dag_id) if workspace_dag else None,
                runs_known=workspace_dag,
            )
        )
    return AutomationsResponse(
        schema_version=AUTOMATIONS_SCHEMA_VERSION,
        checked_at=datetime.now(UTC),
        airflow_available=available,
        automations=automations,
    )


__all__ = (
    "ACTIVE_RUNS_SQL",
    "LAST_RUNS_SQL",
    "NOTES",
    "REGISTERED_DAGS_SQL",
    "describe_cron",
    "describe_schedule",
    "list_automations",
)
