from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.domains.agentops import domain_monitor_support, domain_monitors
from app.services import sync_progress

_LOGGER = logging.getLogger(__name__)


async def run_sync_agentops_monitors(
    *,
    cartridge: str,
    sync_run_id: str,
    user: dict[str, Any] | None,
    ensure_successfactors_talent_monitor: Any,
    list_agents: Any,
    load_agent: Any,
    reserve_scheduled_run: Any,
    execute_reserved_scheduled_monitor: Any,
    sync_agentops_monitor_candidates: Any,
    sync_agentops: Any,
    logger_warning: Any,
) -> dict[str, Any]:
    checked_at_dt = datetime.now(timezone.utc)
    checked_at = checked_at_dt.isoformat()
    sync_fire_at = datetime(1970, 1, 1, tzinfo=timezone.utc)
    schedule_key = sync_agentops.sync_agentops_schedule_key(sync_run_id)
    if cartridge == "sap_successfactors":
        await ensure_successfactors_talent_monitor(user)
    domain_specs = [
        spec
        for spec in domain_monitors.DOMAIN_MONITOR_SPECS
        if spec.cartridge_id == cartridge
    ]
    if domain_specs:
        from app.services import auth as _auth
        from app.services.security_context import (
            build_security_context as _build_security_context,
        )
    for spec in domain_specs:
        await domain_monitor_support.ensure_domain_monitor(
            user,
            spec,
            get_db_pool=_auth.pool,
            build_security_context=_build_security_context,
            logger=_LOGGER,
        )
    agents = await list_agents(
        cartridge_id=cartridge,
        include_inactive=False,
        user_context=user,
    )
    candidates = sync_agentops_monitor_candidates(agents)
    if not candidates:
        return sync_agentops.no_monitor_candidates_payload(checked_at)

    results: list[dict[str, Any]] = []
    completed = 0
    failed = 0
    for agent_row in candidates[:12]:
        agent_id = str(agent_row.get("id") or "").strip()
        agent_slug = str(agent_row.get("slug") or agent_id)
        if not agent_id:
            failed += 1
            results.append(sync_agentops.missing_agent_id_result(agent_slug))
            continue
        try:
            agent = await load_agent(agent_id, user_context=user)
            if not agent:
                raise RuntimeError("agent not found in scoped runtime")
            if not (
                str(agent.tenant_id or "").strip()
                and str(agent.workspace_id or "").strip()
            ):
                raise RuntimeError("monitor agent requires tenant/workspace scope")
            reservation = await reserve_scheduled_run(
                agent_id=str(agent.id),
                tenant_id=str(agent.tenant_id),
                workspace_id=str(agent.workspace_id),
                scheduled_fire_at=sync_fire_at,
                schedule_key=schedule_key,
                airflow_dag_run_id=sync_run_id,
                metadata={
                    "agent_slug": agent.slug,
                    "cartridge_id": agent.cartridge_id,
                    "sync_run_id": sync_run_id,
                    "checked_at": checked_at,
                    "source": "sync-now",
                },
            )
            if reservation.get("duplicate"):
                status = str(reservation.get("status") or "unknown")
                completed_delta, failed_delta = (
                    sync_agentops.duplicate_monitor_counters(status)
                )
                completed += completed_delta
                failed += failed_delta
                results.append(
                    sync_agentops.duplicate_monitor_result(
                        agent_id=agent_id,
                        agent_slug=agent_slug,
                        reservation=reservation,
                    )
                )
                continue
            message = (
                "Ejecuta el monitor operativo posterior a sincronizacion para "
                f"{cartridge}. Usa datos reales recien extraidos; no simules."
            )
            result = await execute_reserved_scheduled_monitor(
                agent=agent,
                message=message,
                reservation=reservation,
                scheduled_fire_at=checked_at,
                metadata={"sync_run_id": sync_run_id, "checked_at": checked_at},
            )
            completed += 1
            results.append(
                sync_agentops.scheduled_monitor_success_result(
                    agent_id=agent_id,
                    agent_slug=agent_slug,
                    result=result if isinstance(result, dict) else None,
                    reservation=reservation,
                )
            )
        except Exception as exc:  # noqa: BLE001
            failed += 1
            logger_warning(
                "sync AgentOps monitor failed cartridge=%s agent=%s: %s",
                cartridge,
                agent_slug,
                exc,
            )
            results.append(
                sync_agentops.scheduled_monitor_failure_result(
                    agent_id=agent_id,
                    agent_slug=agent_slug,
                    exc=exc,
                )
            )

    total = len(candidates[:12])
    return sync_agentops.sync_agentops_summary(
        checked_at=checked_at,
        sync_run_id=sync_run_id,
        total=total,
        completed=completed,
        failed=failed,
        results=results,
    )


async def run_sync_agentops_status(
    *,
    cartridge: str,
    sync_run_id: str,
    running_children: bool,
    bronze_ready: int,
    silver_ready: int,
    gold_ready: int,
    control_room_update: dict[str, Any],
    agentops_refresh: dict[str, Any],
    user: dict[str, Any] | None,
    run_sync_agentops_monitors: Any,
    sync_agentops_is_terminal: Any,
    logger_warning: Any | None = None,
) -> dict[str, Any]:
    if cartridge in domain_monitors.AGENTOPS_MONITOR_CARTRIDGES:
        can_run_agentops = (
            not running_children
            and str(control_room_update.get("status") or "") in {"success", "partial"}
            and bool(bronze_ready or silver_ready or gold_ready)
        )
        if can_run_agentops and not sync_agentops_is_terminal(agentops_refresh):
            try:
                agentops_refresh = await run_sync_agentops_monitors(
                    cartridge=cartridge,
                    sync_run_id=sync_run_id,
                    user=user,
                )
            except Exception as exc:  # noqa: BLE001
                if logger_warning is not None:
                    logger_warning(
                        "sync AgentOps refresh failed cartridge=%s run_id=%s: %s",
                        cartridge,
                        sync_run_id,
                        exc,
                    )
                agentops_refresh = {
                    "status": "failed",
                    "checked_at": datetime.now(timezone.utc).isoformat(),
                    "total": 1,
                    "completed": 0,
                    "failed": 1,
                    "results": [],
                    "reason": f"{type(exc).__name__}: {exc}"[:500],
                }
        update = sync_progress.sync_agents_intelligence_step_update(
            applies=True,
            can_run_agentops=can_run_agentops,
            running_children=running_children,
            agentops_refresh=agentops_refresh,
        )
    else:
        can_run_agentops = False
        agentops_refresh = {}
        update = sync_progress.sync_agents_intelligence_step_update(
            applies=False,
            can_run_agentops=False,
            running_children=running_children,
            agentops_refresh={},
        )

    return {
        "agentops_refresh": agentops_refresh,
        "can_run_agentops": can_run_agentops,
        "update": update,
    }
