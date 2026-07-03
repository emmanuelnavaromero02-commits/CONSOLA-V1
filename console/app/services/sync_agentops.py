from __future__ import annotations

import uuid
from typing import Any


def sync_agentops_schedule_key(sync_run_id: str) -> str:
    return f"sync-now:{uuid.uuid5(uuid.NAMESPACE_URL, sync_run_id)}"


def no_monitor_candidates_payload(checked_at: str) -> dict[str, Any]:
    return {
        "status": "partial",
        "checked_at": checked_at,
        "total": 0,
        "completed": 0,
        "failed": 0,
        "results": [],
        "reason": "No hay monitores activos con contrato AgentOps para este cartucho/workspace.",
    }


def missing_agent_id_result(agent_slug: str) -> dict[str, Any]:
    return {
        "agent_id": "",
        "agent_slug": agent_slug,
        "status": "failed",
        "error": "agent id missing",
    }


def duplicate_monitor_counters(schedule_status: str) -> tuple[int, int]:
    if schedule_status in {"ok", "skipped"}:
        return 1, 0
    if schedule_status in {"error", "cancelled"}:
        return 0, 1
    return 0, 0


def duplicate_monitor_result(
    *,
    agent_id: str,
    agent_slug: str,
    reservation: dict[str, Any],
) -> dict[str, Any]:
    return {
        "agent_id": agent_id,
        "agent_slug": agent_slug,
        "status": "duplicate",
        "schedule_status": str(reservation.get("status") or "unknown"),
        "run_id": reservation.get("agent_run_id"),
        "schedule_run": reservation,
    }


def scheduled_monitor_success_result(
    *,
    agent_id: str,
    agent_slug: str,
    result: dict[str, Any] | None,
    reservation: dict[str, Any],
) -> dict[str, Any]:
    result = result or {}
    return {
        "agent_id": agent_id,
        "agent_slug": agent_slug,
        "status": "success",
        "run_id": result.get("run_id"),
        "schedule_run": reservation,
        "reply": str(result.get("reply") or "")[:500],
        "deterministic_monitor": bool(result.get("deterministic_monitor")),
    }


def scheduled_monitor_failure_result(
    *,
    agent_id: str,
    agent_slug: str,
    exc: Exception,
) -> dict[str, Any]:
    return {
        "agent_id": agent_id,
        "agent_slug": agent_slug,
        "status": "failed",
        "error": f"{type(exc).__name__}: {exc}"[:500],
    }


def sync_agentops_summary(
    *,
    checked_at: str,
    sync_run_id: str,
    total: int,
    completed: int,
    failed: int,
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    status = (
        "success"
        if completed == total
        else "partial"
        if completed or results
        else "failed"
    )
    return {
        "status": status,
        "checked_at": checked_at,
        "sync_run_id": sync_run_id,
        "total": total,
        "completed": completed,
        "failed": failed,
        "results": results,
    }
