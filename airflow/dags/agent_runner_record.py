"""Sanitized durable run recording for the scheduled agent fan-out."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

import requests


def _status(invocation: dict[str, Any]) -> str:
    operational = str(invocation.get("operational_status") or "")
    if operational == "no_eligible_workspaces":
        return "failed"
    results = (
        invocation.get("results") if isinstance(invocation.get("results"), list) else []
    )
    failures = int(invocation.get("scope_failures") or 0)
    ok = sum(
        1
        for item in results
        if isinstance(item.get("status"), int) and int(item["status"]) < 400
    )
    if failures or (results and ok != len(results)):
        return "partial" if ok else "failed"
    return "success"


def record_agent_runner_run(
    context: dict[str, Any],
    *,
    mcp_url: str,
    headers: Callable[[], dict[str, str]],
    cartridge_id: str,
    entity: str,
    dag_id: str,
) -> None:
    invocation = context["ti"].xcom_pull(task_ids="invoke_each") or {}
    logical_date = context["logical_date"]
    ended_at = datetime.now(timezone.utc)
    status = _status(invocation)
    results = (
        invocation.get("results") if isinstance(invocation.get("results"), list) else []
    )
    safe_extra = {
        "operational_status": invocation.get("operational_status"),
        "workspace_count": int(invocation.get("workspace_count") or 0),
        "scope_failures": int(invocation.get("scope_failures") or 0),
        "candidate_count": len(results),
        "successful_count": sum(
            1
            for item in results
            if isinstance(item.get("status"), int) and int(item["status"]) < 400
        ),
    }
    payload = {
        "tool": "pipeline_run_save",
        "args": {
            "cartridge_id": cartridge_id,
            "entity": entity,
            "dag_id": dag_id,
            "run_id": f"{dag_id}:{context['run_id']}",
            "airflow_dag_run_id": context["run_id"],
            "mode": "scheduled",
            "status": status,
            "started_at": logical_date.isoformat(),
            "finished_at": ended_at.isoformat(),
            "duration_seconds": max(0.0, (ended_at - logical_date).total_seconds()),
            "extra": safe_extra,
        },
    }
    try:
        response = requests.post(
            f"{mcp_url}/mcp/invoke",
            json=payload,
            headers=headers(),
            timeout=15,
        )
        body = response.json() if response.status_code < 400 else {}
        if response.status_code >= 400 or body.get("error"):
            raise RuntimeError("pipeline registry rejected agent runner result")
    except Exception as exc:
        raise RuntimeError("agent runner result could not be recorded") from exc
    if status != "success":
        raise RuntimeError("agent runner completed with an operational failure")
