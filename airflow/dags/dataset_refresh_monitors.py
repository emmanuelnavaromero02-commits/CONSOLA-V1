from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Any

import requests

try:
    from agent_runner_outcome import require_registry_save, require_scheduled_invocation
    from runtime_security_context import build_pipeline_run_context
except ModuleNotFoundError:
    from airflow.dags.agent_runner_outcome import (
        require_registry_save,
        require_scheduled_invocation,
    )
    from airflow.dags.runtime_security_context import build_pipeline_run_context


MONITOR_ENTITY = "AgentOpsMonitors"
DUE_PATH = "/internal/automation/refresh-monitors/due"
INVOKE_PATH = "/internal/automation/refresh-monitors/invoke"
NARRATE_PATH = "/api/operations/internal/control-room/narrate-alerts"
DUE_TIMEOUT_SECONDS = 120
INVOKE_TIMEOUT_SECONDS = 600
NARRATE_TIMEOUT_SECONDS = 210
_NARRATION_COUNTERS = (
    "candidates",
    "narrated_ready",
    "narrated_template",
    "skipped_current",
    "budget_exhausted",
    "deferred",
)
BUDGET_SECONDS = 20 * 60
_MAX_RESULTS = 12


def _outcome(status: str, *, reason: str | None = None, **counts: int) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": status,
        "total": 0,
        "completed": 0,
        "failed": 0,
        "deferred": 0,
        "duplicates": 0,
        "results": [],
    }
    payload.update(counts)
    if reason:
        payload["reason"] = reason
    return payload


def _discover(
    *, url: str, headers: Mapping[str, str], body: Mapping[str, Any], http: Any
) -> dict[str, Any]:
    response = http.post(url, headers=dict(headers), json=dict(body), timeout=DUE_TIMEOUT_SECONDS)
    if not 200 <= int(getattr(response, "status_code", 0)) < 300:
        raise RuntimeError("refresh monitor discovery rejected")
    payload = response.json()
    due = payload.get("due") if isinstance(payload, Mapping) else None
    if (
        not isinstance(payload, Mapping)
        or payload.get("status") not in {"ready", "no_monitors"}
        or not isinstance(due, list)
    ):
        raise RuntimeError("refresh monitor discovery returned an invalid state")
    agents = []
    for item in due[:_MAX_RESULTS]:
        if not isinstance(item, Mapping) or not str(item.get("id") or "").strip():
            raise RuntimeError("refresh monitor discovery returned an invalid agent")
        agents.append({"id": str(item["id"]), "slug": str(item.get("slug") or "")[:120]})
    return {"status": payload["status"], "due": agents}


def narrate_alerts(
    *,
    url: str,
    headers: Mapping[str, str],
    tenant_id: str,
    workspace_id: str,
    http: Any = requests,
) -> dict[str, Any]:
    try:
        response = http.post(
            url,
            headers=dict(headers),
            json={"tenant_id": tenant_id, "workspace_id": workspace_id},
            timeout=NARRATE_TIMEOUT_SECONDS,
        )
        status_code = int(getattr(response, "status_code", 0))
        payload = response.json() if 200 <= status_code < 300 else None
        if not isinstance(payload, Mapping):
            raise RuntimeError("alert narration rejected")
    except Exception as exc:  # noqa: BLE001 - advisory step, reported in the outcome
        return {"status": "unavailable", "error_code": type(exc).__name__}
    summary: dict[str, Any] = {
        name: int(payload.get(name) or 0) for name in _NARRATION_COUNTERS
    }
    summary["status"] = str(payload.get("status") or "")
    summary["failures"] = len(payload.get("failures") or [])
    return summary


def run_refresh_monitors(
    *,
    console_url: str,
    headers: Callable[[], Mapping[str, str]],
    runner_token: str,
    tenant_id: str,
    workspace_id: str,
    refresh_run_id: str,
    airflow_dag_run_id: str,
    http: Any = requests,
    clock: Callable[[], float] = time.monotonic,
    budget_seconds: float = BUDGET_SECONDS,
) -> dict[str, Any]:
    if not str(runner_token or "").strip():
        return _outcome("failed", reason="runner_authority_unavailable")
    scope = {
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "refresh_run_id": refresh_run_id,
    }
    base = console_url.rstrip("/")
    try:
        base_headers = dict(headers())
        request_headers = {**base_headers, "X-Agent-Runner-Token": runner_token}
        discovery = _discover(
            url=f"{base}{DUE_PATH}", headers=request_headers, body=scope, http=http
        )
    except Exception as exc:  # noqa: BLE001 - recorded, never fails the refresh chain
        return _outcome("failed", reason=f"discovery_unavailable:{type(exc).__name__}")
    due = discovery["due"]
    if discovery["status"] == "no_monitors" or not due:
        return _outcome("skipped", reason="no_monitors")

    results: list[dict[str, Any]] = []
    completed = failed = deferred = duplicates = 0
    started = clock()
    for agent in due:
        if clock() - started > budget_seconds:
            deferred += 1
            results.append({"slug": agent["slug"], "status": "deferred"})
            continue
        response = None
        try:
            response = http.post(
                f"{base}{INVOKE_PATH}",
                headers=request_headers,
                json={
                    **scope,
                    "agent_id": agent["id"],
                    "airflow_dag_run_id": airflow_dag_run_id,
                },
                timeout=INVOKE_TIMEOUT_SECONDS,
            )
            checked = require_scheduled_invocation(response)
        except Exception as exc:  # noqa: BLE001 - one monitor must not hide the others
            failed += 1
            results.append(
                {
                    "slug": agent["slug"],
                    "status": "failed",
                    "http_status": getattr(response, "status_code", None),
                    "error_code": type(exc).__name__,
                }
            )
            continue
        completed += 1
        duplicates += int(checked["duplicate"])
        results.append(
            {
                "slug": agent["slug"],
                "status": "duplicate" if checked["duplicate"] else "completed",
                "run_id": checked["run_id"],
            }
        )
    total = len(due)
    status = "success" if completed == total else "partial" if completed else "failed"
    outcome = _outcome(
        status,
        reason=None if status == "success" else "monitors_incomplete",
        total=total,
        completed=completed,
        failed=failed,
        deferred=deferred,
        duplicates=duplicates,
        results=results,
    )
    if completed:
        narration = narrate_alerts(
            url=f"{base}{NARRATE_PATH}",
            headers=base_headers,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            http=http,
        )
        outcome["narration"] = narration
        if status == "success" and narration.get("status") not in {
            "ready",
            "no_eligible_workspaces",
        }:
            outcome["status"] = "partial"
            outcome["reason"] = "narration_unavailable"
    return outcome


def record_refresh_monitors(
    outcome: Mapping[str, Any],
    *,
    mcp_url: str,
    headers: Mapping[str, str],
    cartridge_id: str,
    tenant_id: str,
    workspace_id: str,
    airflow_run_id: str,
    started_at: datetime,
    http: Any = requests,
) -> None:
    status = str(outcome.get("status") or "failed")
    finished_at = datetime.now(timezone.utc)
    extra = {
        key: outcome.get(key)
        for key in ("total", "completed", "failed", "deferred", "duplicates", "reason")
        if outcome.get(key) is not None
    }
    narration = outcome.get("narration")
    if isinstance(narration, Mapping):
        extra["narration"] = {
            key: narration.get(key)
            for key in ("status", "error_code", "failures", *_NARRATION_COUNTERS)
            if narration.get(key) is not None
        }
    extra["results"] = [
        {
            key: item.get(key)
            for key in ("slug", "status", "run_id", "http_status", "error_code")
            if item.get(key) is not None
        }
        for item in (outcome.get("results") or [])[:_MAX_RESULTS]
        if isinstance(item, Mapping)
    ]
    args: dict[str, Any] = {
        "dag_id": "dataset_refresh_chain",
        "cartridge_id": cartridge_id,
        "entity": MONITOR_ENTITY,
        "run_id": f"dataset_refresh_chain:{airflow_run_id}:monitors",
        "airflow_dag_run_id": airflow_run_id,
        "mode": "monitors",
        "status": status,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "extra": extra,
    }
    if status not in {"success", "skipped"} and outcome.get("reason"):
        args["error_message"] = str(outcome["reason"])[:300]
    try:
        response = http.post(
            f"{mcp_url.rstrip('/')}/mcp/invoke",
            headers=dict(headers),
            json={
                "tool": "pipeline_run_save",
                "args": args,
                "security_context": build_pipeline_run_context(args),
            },
            timeout=15,
        )
        require_registry_save(response, expected_status=status)
    except Exception as exc:
        raise RuntimeError("refresh monitor outcome could not be recorded") from exc
