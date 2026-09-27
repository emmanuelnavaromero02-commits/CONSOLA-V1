from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
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
from app.services.permissions import user_role


PLATFORM_CARTRIDGE = "platform"
PLATFORM_ADMIN_ROLES = frozenset({"owner", "super_admin", "admin"})
READ_ONLY_TRANSACTION_SQL = "SET TRANSACTION READ ONLY"
RUNS_PER_DAG = 10
AIRFLOW_LIST_TIMEOUT_SECONDS = 8.0
AIRFLOW_RUNS_TIMEOUT_SECONDS = 5.0
AIRFLOW_CONCURRENCY = 4
ACTIVE_RUN_STATES = frozenset({"queued", "running"})
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


def _datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


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


def _runs_summary(
    payload: Mapping[str, Any] | None,
) -> tuple[int | None, bool, AutomationRun | None]:
    if not isinstance(payload, Mapping) or payload.get("error"):
        return None, False, None
    runs = [run for run in payload.get("runs") or [] if isinstance(run, Mapping)]
    active = sum(1 for run in runs if str(run.get("state") or "") in ACTIVE_RUN_STATES)
    last = None
    if runs:
        first = runs[0]
        status = str(first.get("state") or "").strip().lower()
        if re.fullmatch(r"[a-z_]{1,40}", status):
            last = AutomationRun(
                status=status,
                started_at=_datetime(first.get("start_date")),
                finished_at=_datetime(first.get("end_date")),
            )
    return active, active >= RUNS_PER_DAG, last


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


async def _registered_dags(user: Mapping[str, Any]) -> list[dict[str, Any]]:
    cartridges = _visible_cartridges(user)
    if cartridges is not None and not cartridges:
        return []
    pool = await auth.pool()

    async def _read(conn: Any, _tenant: str | None, _workspace: str) -> list[Any]:
        await conn.execute(READ_ONLY_TRANSACTION_SQL)
        return list(await conn.fetch(REGISTERED_DAGS_SQL, cartridges, MAX_AUTOMATIONS))

    rows = [dict(row) for row in await run_with_db_scope(pool, dict(user), _read)]
    platform = is_platform_admin(user)
    return [
        row
        for row in rows
        if DAG_ID.fullmatch(str(row.get("dag_id") or ""))
        and (platform or str(row.get("cartridge_id") or "") != PLATFORM_CARTRIDGE)
    ]


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


async def _runs(
    dag_ids: Sequence[str], user: Mapping[str, Any], invoke: Invoker
) -> dict[str, Mapping[str, Any] | None]:
    gate = asyncio.Semaphore(AIRFLOW_CONCURRENCY)

    async def _one(dag_id: str) -> tuple[str, Mapping[str, Any] | None]:
        async with gate:
            try:
                result = await asyncio.wait_for(
                    invoke(
                        "airflow_list_dag_runs",
                        {"dag_id": dag_id, "limit": RUNS_PER_DAG},
                        user,
                    ),
                    timeout=AIRFLOW_RUNS_TIMEOUT_SECONDS,
                )
            except Exception:
                return dag_id, None
        return dag_id, result if isinstance(result, Mapping) else None

    return dict(await asyncio.gather(*(_one(dag_id) for dag_id in dag_ids)))


async def list_automations(
    user: Mapping[str, Any], *, invoke: Invoker | None = None
) -> AutomationsResponse:
    call = invoke or _default_invoke
    registered = await _registered_dags(user)
    airflow = await _airflow_dags(user, call)
    available = airflow is not None
    present = [
        str(row["dag_id"])
        for row in registered
        if available and str(row["dag_id"]) in (airflow or {})
    ]
    runs = await _runs(present, user, call) if present else {}
    automations: list[Automation] = []
    for row in registered:
        dag_id = str(row["dag_id"])
        dag = (airflow or {}).get(dag_id)
        kind = _kind(row, dag)
        state, note = _state(dag_id, kind, dag, airflow_available=available)
        active, capped, last = _runs_summary(runs.get(dag_id))
        cartridge = str(row.get("cartridge_id") or "").strip()
        automations.append(
            Automation(
                dag_id=dag_id,
                label=_label(row, dag),
                cartridge_id=(
                    cartridge
                    if CARTRIDGE_ID.fullmatch(cartridge)
                    and cartridge != PLATFORM_CARTRIDGE
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
                active_runs=active,
                active_runs_capped=capped,
                last_run=last,
            )
        )
    return AutomationsResponse(
        schema_version=AUTOMATIONS_SCHEMA_VERSION,
        checked_at=datetime.now(UTC),
        airflow_available=available,
        automations=automations,
    )


__all__ = (
    "NOTES",
    "REGISTERED_DAGS_SQL",
    "describe_cron",
    "describe_schedule",
    "list_automations",
)
