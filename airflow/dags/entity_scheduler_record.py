"""Operational run recording for ``entity_scheduler``."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

import requests
from runtime_security_context import build_pipeline_run_context


def record_scheduler_run(
    *,
    context: Mapping[str, Any],
    invocation: Mapping[str, Any],
    mcp_url: str,
    headers: Mapping[str, str],
    http: Any = requests,
) -> None:
    """Persist the scheduler result through MCP infrastructure."""
    triggered = int(invocation.get("triggered") or 0)
    results = invocation.get("results") or []
    total = len(results)
    status = "success" if total == triggered else "partial" if triggered else "failed"
    if total == 0:
        status = "success"

    payload = {
        "tool": "pipeline_run_save",
        "args": {
            "dag_id": "entity_scheduler",
            "cartridge_id": "platform",
            "entity": "EntityScheduler",
            "run_id": f"entity_scheduler:{context['run_id']}",
            "airflow_dag_run_id": context["run_id"],
            "mode": "scheduled",
            "status": status,
            "started_at": context["logical_date"].isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "extra": dict(invocation),
        },
    }
    payload["security_context"] = build_pipeline_run_context(payload["args"])
    response = http.post(
        f"{mcp_url}/mcp/invoke",
        headers=dict(headers),
        json=payload,
        timeout=15,
    )
    print("[entity_scheduler] pipeline_run_save " f"status={response.status_code}")
    response.raise_for_status()
    body = response.json()
    if body.get("error"):
        raise RuntimeError("pipeline_run_save returned an error")
