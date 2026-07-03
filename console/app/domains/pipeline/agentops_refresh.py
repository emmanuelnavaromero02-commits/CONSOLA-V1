from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


async def run_sync_agentops_monitors(
    *,
    cartridge: str,
    sync_run_id: str,
    user: dict[str, Any] | None,
    ensure_successfactors_talent_monitor: Any,
    list_agents: Any,
    load_agent: Any,
    reserve_scheduled_run: Any,
    run_scheduled_monitor: Any,
    finish_scheduled_run: Any,
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
            try:
                result = await run_scheduled_monitor(
                    agent,
                    message,
                    scheduled_fire_at=checked_at,
                )
            except Exception as exc:
                await finish_scheduled_run(
                    schedule_run_id=reservation.get("id"),
                    agent_run_id=None,
                    status="error",
                    tenant_id=str(agent.tenant_id),
                    workspace_id=str(agent.workspace_id),
                    error_message=f"{type(exc).__name__}: {exc}",
                    metadata={"sync_run_id": sync_run_id, "checked_at": checked_at},
                )
                raise
            await finish_scheduled_run(
                schedule_run_id=reservation.get("id"),
                agent_run_id=result.get("run_id") if isinstance(result, dict) else None,
                status="ok",
                tenant_id=str(agent.tenant_id),
                workspace_id=str(agent.workspace_id),
                metadata={
                    "sync_run_id": sync_run_id,
                    "checked_at": checked_at,
                    "deterministic_monitor": bool(
                        isinstance(result, dict)
                        and result.get("deterministic_monitor")
                    ),
                },
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
