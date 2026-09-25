from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

import requests

from agent_runner_outcome import require_registry_save
from runtime_security_context import build_pipeline_run_context


def _status(invocation: dict[str, Any]) -> str:
    operational = str(invocation.get("operational_status") or "")
    if operational == "no_eligible_workspaces":
        return "failed"
    results = (
        invocation.get("results") if isinstance(invocation.get("results"), list) else []
    )
    failures = int(invocation.get("scope_failures") or 0)
    ok = sum(1 for item in results if isinstance(item, dict) and item.get("ok") is True)
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
            1 for item in results if isinstance(item, dict) and item.get("ok") is True
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
    payload["security_context"] = build_pipeline_run_context(payload["args"])
    try:
        response = requests.post(
            f"{mcp_url}/mcp/invoke",
            json=payload,
            headers=headers(),
            timeout=15,
        )
        require_registry_save(response, expected_status=status)
    except Exception as exc:
        raise RuntimeError("agent runner result could not be recorded") from exc
    if status != "success":
        raise RuntimeError("agent runner completed with an operational failure")
