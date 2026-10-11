from __future__ import annotations

import json
import logging
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

import httpx
from fastapi import HTTPException

from app.domains.pipeline.extract_config import connection_id_from_vault_payload
from app.services.db_scope import scoped_db


logger = logging.getLogger(__name__)

SUCCESSFACTORS = "sap_successfactors"
REFRESH_DAG_ID = "dataset_refresh_chain"
REFRESH_ENTITY = "DatasetRefreshChain"
REFRESH_STATUSES = frozenset({"success", "partial"})
REFRESH_MAX_AGE = timedelta(hours=24)
REFRESH_FIRE_AT = datetime(1970, 1, 1, tzinfo=timezone.utc)
MAX_REFRESH_MONITORS = 12
_PAGE_SIZE = 200
_VAULT_URL = os.environ.get("VAULT_URL", "http://vault:8300").rstrip("/")


def require_airflow(internal_service: str) -> None:
    if internal_service != "airflow":
        raise HTTPException(403, "only airflow can run scheduled automation")


def require_runner_token(token: str | None) -> None:
    expected = os.environ.get("AGENT_RUNNER_TOKEN", "")
    if not expected or not secrets.compare_digest(str(token or ""), expected):
        raise HTTPException(401, "invalid runner token")


def refresh_schedule_key(refresh_run_id: str) -> str:
    return f"after-refresh:{uuid.uuid5(uuid.NAMESPACE_URL, refresh_run_id)}"


def service_user(
    tenant_id: str, workspace_id: str, cartridge_id: str, principal: str
) -> dict[str, Any]:
    return {
        "id": None,
        "email": principal,
        "role": "viewer",
        "active_tenant_id": tenant_id,
        "active_workspace_id": workspace_id,
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "allowed_cartridges": [cartridge_id],
    }


async def active_workspaces(pool: Any) -> list[dict[str, str]]:
    scopes: list[dict[str, str]] = []
    cursor_created: datetime | None = None
    cursor_id: str | None = None
    while True:
        rows = await pool.fetch(
            """
            SELECT w.tenant_id::text AS tenant_id, w.id::text AS workspace_id,
                   w.created_at
              FROM workspaces w
              JOIN tenants t ON t.id = w.tenant_id
             WHERE t.status = 'active'
               AND ($1::timestamptz IS NULL OR (w.created_at, w.id) > ($1, $2::uuid))
             ORDER BY w.created_at, w.id
             LIMIT $3
            """,
            cursor_created,
            cursor_id,
            _PAGE_SIZE,
        )
        scopes.extend(
            {"tenant_id": str(row["tenant_id"]), "workspace_id": str(row["workspace_id"])}
            for row in rows
        )
        if len(rows) < _PAGE_SIZE:
            return scopes
        cursor_created = rows[-1]["created_at"]
        cursor_id = str(rows[-1]["workspace_id"])


def enabled_connection_id(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    connections = [
        item
        for item in payload.get("connections") or []
        if isinstance(item, dict) and item.get("enabled") is not False
    ]
    return connection_id_from_vault_payload({"connections": connections})


async def _vault_connections(scope: dict[str, str], *, cartridge_id: str) -> Any:
    from app.security import get_internal_api_key
    from app.services.security_context import build_security_context

    user = service_user(
        scope["tenant_id"],
        scope["workspace_id"],
        cartridge_id,
        "system:scheduled-extraction",
    )
    headers = {
        "x-api-key": os.environ.get("INTERNAL_API_KEY_CONSOLE_TO_VAULT")
        or get_internal_api_key(),
        "x-internal-service": "console",
        "x-security-context": json.dumps(
            build_security_context(user), ensure_ascii=False
        ),
    }
    async with httpx.AsyncClient(headers=headers, timeout=10) as client:
        response = await client.get(
            f"{_VAULT_URL}/connections/{quote(cartridge_id, safe='')}"
        )
    if response.status_code in {204, 404}:
        return None
    response.raise_for_status()
    return response.json()


async def successfactors_targets(
    pool: Any, *, load_connections: Any = None
) -> dict[str, Any]:
    loader = load_connections or _vault_connections
    scopes = await active_workspaces(pool)
    if not scopes:
        return {
            "status": "no_eligible_workspaces",
            "targets": [],
            "workspaces": 0,
            "failures": [],
        }
    targets: list[dict[str, str]] = []
    failures: list[dict[str, str]] = []
    for scope in scopes:
        try:
            conn_id = enabled_connection_id(
                await loader(scope, cartridge_id=SUCCESSFACTORS)
            )
        except Exception as exc:  # noqa: BLE001 - isolate scopes, report class only
            failures.append(
                {"workspace_id": scope["workspace_id"], "error_code": type(exc).__name__}
            )
            continue
        if conn_id:
            targets.append({**scope, "conn_id": conn_id})
    return {
        "status": "partial" if failures else "ready",
        "targets": targets,
        "workspaces": len(scopes),
        "failures": failures,
    }


def _aware(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


async def recorded_refresh(
    pool: Any,
    *,
    tenant_id: str,
    workspace_id: str,
    refresh_run_id: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    async with scoped_db(pool, tenant_id, workspace_id) as conn:
        row = await conn.fetchrow(
            """
            SELECT run_id, cartridge_id, status, finished_at
              FROM pipeline_runs
             WHERE run_id = $1
               AND dag_id = $2
               AND entity = $3
               AND tenant_id = $4::uuid
               AND workspace_id = $5::uuid
            """,
            refresh_run_id,
            REFRESH_DAG_ID,
            REFRESH_ENTITY,
            tenant_id,
            workspace_id,
        )
    if row is None:
        raise HTTPException(409, "refresh run is not recorded for this workspace")
    if str(row["status"] or "") not in REFRESH_STATUSES:
        raise HTTPException(409, "refresh run did not publish data")
    finished_at = _aware(row["finished_at"])
    clock = now or datetime.now(timezone.utc)
    if finished_at is None or clock - finished_at > REFRESH_MAX_AGE:
        raise HTTPException(409, "refresh run is not recent")
    cartridge_id = str(row["cartridge_id"] or "").strip()
    if not cartridge_id:
        raise HTTPException(409, "refresh run has no data source")
    return {
        "run_id": str(row["run_id"]),
        "cartridge_id": cartridge_id,
        "status": str(row["status"]),
        "finished_at": finished_at.isoformat(),
    }


def monitor_contract_ok(extra: Any) -> bool:
    if not isinstance(extra, dict):
        return False
    schedule = extra.get("schedule")
    if isinstance(schedule, dict) and schedule.get("enabled") is False:
        return False
    monitor = extra.get("monitor")
    return (
        str(extra.get("role") or "").strip().lower() == "monitor"
        and isinstance(monitor, dict)
        and bool(monitor)
    )


async def _ensure_monitors(tenant_id: str, workspace_id: str, cartridge_id: str) -> None:
    from app.domains.agentops import domain_monitor_support, domain_monitors
    from app.domains.agentops.successfactors_talent_monitor import (
        ensure_successfactors_talent_monitor,
    )
    from app.services import auth
    from app.services.security_context import build_security_context

    user = service_user(tenant_id, workspace_id, cartridge_id, "system:refresh-monitors")
    if cartridge_id == SUCCESSFACTORS:
        await ensure_successfactors_talent_monitor(
            user,
            get_db_pool=auth.pool,
            build_security_context=build_security_context,
            logger=logger,
        )
    for spec in domain_monitors.ALL_DOMAIN_MONITOR_SPECS:
        if spec.cartridge_id == cartridge_id:
            await domain_monitor_support.ensure_domain_monitor(
                user,
                spec,
                get_db_pool=auth.pool,
                build_security_context=build_security_context,
                logger=logger,
            )


async def refresh_monitors_due(
    pool: Any,
    *,
    tenant_id: str,
    workspace_id: str,
    refresh_run_id: str,
    ensure_monitors: Any = None,
) -> dict[str, Any]:
    refresh = await recorded_refresh(
        pool,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        refresh_run_id=refresh_run_id,
    )
    await (ensure_monitors or _ensure_monitors)(
        tenant_id, workspace_id, refresh["cartridge_id"]
    )
    async with scoped_db(pool, tenant_id, workspace_id) as conn:
        rows = await conn.fetch(
            """
            SELECT id::text AS id, slug, extra
              FROM agents
             WHERE tenant_id = $1::uuid
               AND workspace_id = $2::uuid
               AND cartridge_id = $3
               AND is_active = TRUE
             ORDER BY slug, id
            """,
            tenant_id,
            workspace_id,
            refresh["cartridge_id"],
        )
    due = []
    for row in rows:
        extra = row["extra"]
        if isinstance(extra, str):
            try:
                extra = json.loads(extra)
            except ValueError:
                extra = {}
        if monitor_contract_ok(extra):
            due.append({"id": str(row["id"]), "slug": str(row["slug"])})
    return {
        "status": "ready" if due else "no_monitors",
        "cartridge_id": refresh["cartridge_id"],
        "refresh_status": refresh["status"],
        "due": due[:MAX_REFRESH_MONITORS],
    }


async def invoke_refresh_monitor(
    pool: Any,
    *,
    agent_id: str,
    tenant_id: str,
    workspace_id: str,
    refresh_run_id: str,
    airflow_dag_run_id: str | None,
    load_agent: Any = None,
    reserve_scheduled_run: Any = None,
    execute_reserved_scheduled_monitor: Any = None,
) -> dict[str, Any]:
    if load_agent is None:
        from app.services.agent_runtime import load_agent
    if reserve_scheduled_run is None:
        from app.services.agent_scheduler import reserve_scheduled_run
    if execute_reserved_scheduled_monitor is None:
        from app.services.scheduled_monitor_execution import (
            execute_reserved_scheduled_monitor,
        )

    refresh = await recorded_refresh(
        pool,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        refresh_run_id=refresh_run_id,
    )
    agent = await load_agent(
        agent_id, user_context={"tenant_id": tenant_id, "workspace_id": workspace_id}
    )
    if not agent:
        raise HTTPException(404, "agent not found")
    if (
        str(getattr(agent, "tenant_id", "") or "") != tenant_id
        or str(getattr(agent, "workspace_id", "") or "") != workspace_id
    ):
        raise HTTPException(403, "monitor belongs to another workspace")
    if str(getattr(agent, "cartridge_id", "") or "") != refresh["cartridge_id"]:
        raise HTTPException(403, "monitor does not watch the refreshed data source")
    if not getattr(agent, "is_active", True) or not monitor_contract_ok(
        getattr(agent, "extra", None)
    ):
        raise HTTPException(
            403, "scheduled agents require monitor role and monitor contract"
        )
    reservation = await reserve_scheduled_run(
        agent_id=str(agent.id),
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        scheduled_fire_at=REFRESH_FIRE_AT,
        schedule_key=refresh_schedule_key(refresh["run_id"]),
        airflow_dag_run_id=airflow_dag_run_id,
        metadata={
            "agent_slug": agent.slug,
            "cartridge_id": agent.cartridge_id,
            "refresh_run_id": refresh["run_id"],
            "source": "after-refresh",
        },
    )
    if reservation.get("duplicate"):
        completed = reservation.get("status") == "ok" and isinstance(
            reservation.get("agent_run_id"), int
        )
        return {
            "ok": completed,
            "status": "completed"
            if completed
            else str(reservation.get("status") or "error"),
            "agent_id": agent.id,
            "run_id": reservation.get("agent_run_id"),
            "duplicate": True,
            "schedule_run": reservation,
        }
    result = await execute_reserved_scheduled_monitor(
        agent=agent,
        message=(
            "Ejecuta el monitor operativo posterior a la actualización de datos. "
            "Usa solo datos reales recién publicados."
        ),
        reservation=reservation,
        scheduled_fire_at=refresh["finished_at"],
        metadata={
            "refresh_run_id": refresh["run_id"],
            "airflow_dag_run_id": airflow_dag_run_id,
        },
    )
    result["ok"] = True
    result["status"] = "completed"
    result["duplicate"] = False
    result["schedule_run"] = {
        **reservation,
        "status": "ok",
        "agent_run_id": result.get("run_id"),
    }
    return result
