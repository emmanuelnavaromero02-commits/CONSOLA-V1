from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests

try:
    from entity_scheduler_trigger import AirflowTriggerError, trigger_dag_run
    from runtime_security_context import (
        build_pipeline_run_context,
        build_scheduled_run_context,
    )
except ModuleNotFoundError:
    from airflow.dags.entity_scheduler_trigger import AirflowTriggerError, trigger_dag_run
    from airflow.dags.runtime_security_context import (
        build_pipeline_run_context,
        build_scheduled_run_context,
    )


DAILY_DAG_ID = "sap_successfactors_daily_extract"
CARTRIDGE_ID = "sap_successfactors"
EXTRACT_ALL_DAG_ID = "sap_successfactors_extract_all"
ACTIVE_DAG_IDS = (EXTRACT_ALL_DAG_ID, "sap_successfactors_extract")
SCHEDULE_TIMEZONE = "America/Mexico_City"
SCHEDULE_CRON_UTC = "0 12 * * *"
RECORD_ENTITY = "SuccessFactorsDailyExtract"
AGGREGATE_ENTITY = "__extract_all__"
TARGETS_PATH = "/internal/automation/successfactors/daily-targets"
LATE_TOLERANCE = timedelta(hours=2)
STALE_QUEUED_AFTER = timedelta(hours=6)
STALE_RUNNING_AFTER = timedelta(hours=12)
STALE_RUN_MESSAGE = "Bloqueada por corrida atascada de SuccessFactors"
_STALE_RUNS_REPORTED = 5
_STALE_ANCHORS = {
    "queued": ("queued_at", "start_date", "logical_date", "execution_date"),
    "running": ("start_date", "queued_at", "logical_date", "execution_date"),
}
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_CONN_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


class TargetDiscoveryError(RuntimeError):
    pass


def _aware(value: Any) -> datetime | None:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _run_type(context: Mapping[str, Any]) -> str:
    value = getattr(context.get("dag_run"), "run_type", None)
    return str(getattr(value, "value", value) or "").strip().lower()


def _local_day(moment: datetime) -> str:
    return moment.astimezone(ZoneInfo(SCHEDULE_TIMEZONE)).date().isoformat()


def run_window(context: Mapping[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    current = _aware(now) or datetime.now(timezone.utc)
    end = _aware(context.get("data_interval_end"))
    if _run_type(context) == "scheduled" and end is not None:
        return {"day": _local_day(end), "late": current - end > LATE_TOLERANCE}
    return {"day": _local_day(current), "late": False}


def daily_run_id(day: str, workspace_id: str) -> str:
    return f"sched_sf_daily_{day.replace('-', '')}_{workspace_id}"


def parse_targets(payload: Any) -> list[dict[str, str]]:
    if not isinstance(payload, Mapping) or payload.get("status") not in {
        "ready",
        "partial",
        "no_eligible_workspaces",
    }:
        raise TargetDiscoveryError("daily target discovery returned an invalid state")
    raw = payload.get("targets")
    if not isinstance(raw, list):
        raise TargetDiscoveryError("daily target discovery returned no target list")
    targets: dict[str, dict[str, str]] = {}
    for item in raw:
        if not isinstance(item, Mapping):
            raise TargetDiscoveryError("daily target is malformed")
        tenant = str(item.get("tenant_id") or "").strip().lower()
        workspace = str(item.get("workspace_id") or "").strip().lower()
        conn_id = str(item.get("conn_id") or "").strip()
        if not (_UUID.fullmatch(tenant) and _UUID.fullmatch(workspace) and _CONN_ID.fullmatch(conn_id)):
            raise TargetDiscoveryError("daily target scope is invalid")
        targets.setdefault(
            workspace, {"tenant_id": tenant, "workspace_id": workspace, "conn_id": conn_id}
        )
    return [targets[key] for key in sorted(targets)]


def discover_targets(
    *, console_url: str, headers: Mapping[str, str], http: Any = requests
) -> dict[str, Any]:
    try:
        response = http.post(
            f"{console_url.rstrip('/')}{TARGETS_PATH}",
            headers=dict(headers),
            json={},
            timeout=60,
        )
    except requests.RequestException as exc:
        raise TargetDiscoveryError("daily target discovery unavailable") from exc
    if not 200 <= int(getattr(response, "status_code", 0)) < 300:
        raise TargetDiscoveryError("daily target discovery rejected")
    payload = response.json()
    return {
        "status": str(payload.get("status")) if isinstance(payload, Mapping) else "",
        "targets": parse_targets(payload),
        "scope_failures": len(payload.get("failures") or [])
        if isinstance(payload, Mapping)
        else 0,
    }


def _run_workspace(run: Mapping[str, Any]) -> str | None:
    conf = run.get("conf") if isinstance(run.get("conf"), Mapping) else {}
    value = str(conf.get("workspace_id") or "").strip().lower()
    return value or None


def _run_age(run: Mapping[str, Any], *, now: datetime) -> timedelta | None:
    state = str(run.get("state") or "").strip().lower()
    anchor = next(
        (
            moment
            for key in _STALE_ANCHORS.get(state, ())
            if (moment := _aware(run.get(key))) is not None
        ),
        None,
    )
    return None if anchor is None else now - anchor


def _run_is_stale(run: Mapping[str, Any], *, paused: bool, now: datetime) -> bool:
    if paused:
        return True
    age = _run_age(run, now=now)
    if age is None:
        return False
    state = str(run.get("state") or "").strip().lower()
    limit = STALE_QUEUED_AFTER if state == "queued" else STALE_RUNNING_AFTER
    return age >= limit


def _stale_entry(
    dag_id: str, run: Mapping[str, Any], *, paused: bool, now: datetime
) -> dict[str, Any]:
    age = _run_age(run, now=now)
    return {
        "dag_id": dag_id,
        "dag_run_id": str(run.get("dag_run_id") or "")[:250],
        "state": str(run.get("state") or "").strip().lower(),
        "dag_paused": paused,
        "age_hours": None if age is None else round(age.total_seconds() / 3600, 1),
    }


def _dag_paused(
    dag_url: str, dag_id: str, *, auth: tuple[str, str], http: Any, timeout: int
) -> bool | None:
    try:
        response = http.get(dag_url, auth=auth, timeout=timeout)
    except requests.RequestException as exc:
        raise AirflowTriggerError("describe", dag_id) from exc
    if response.status_code == 404:
        return None
    payload = response.json() if response.status_code == 200 else None
    if not isinstance(payload, Mapping) or not isinstance(payload.get("is_paused"), bool):
        raise AirflowTriggerError("describe", dag_id, response.status_code)
    return payload["is_paused"]


def active_extraction_workspaces(
    *,
    base_url: str,
    username: str,
    password: str,
    http: Any = requests,
    timeout: int = 30,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = _aware(now) or datetime.now(timezone.utc)
    auth = (username, password)
    running: set[str] = set()
    stale: dict[str, list[dict[str, Any]]] = {}
    unscoped_stale: list[dict[str, Any]] = []
    unscoped = False
    for dag_id in ACTIVE_DAG_IDS:
        dag_url = f"{base_url.rstrip('/')}/api/v1/dags/{quote(dag_id, safe='')}"
        paused = _dag_paused(dag_url, dag_id, auth=auth, http=http, timeout=timeout)
        if paused is None:
            continue
        try:
            response = http.get(
                f"{dag_url}/dagRuns",
                auth=auth,
                params=[("state", "queued"), ("state", "running"), ("limit", "100")],
                timeout=timeout,
            )
        except requests.RequestException as exc:
            raise AirflowTriggerError("list", dag_id) from exc
        if response.status_code == 404:
            continue
        if response.status_code != 200:
            raise AirflowTriggerError("list", dag_id, response.status_code)
        runs = response.json().get("dag_runs")
        if not isinstance(runs, list):
            raise AirflowTriggerError("list", dag_id, response.status_code)
        for run in runs:
            if not isinstance(run, Mapping):
                continue
            workspace = _run_workspace(run)
            is_stale = _run_is_stale(run, paused=paused, now=current)
            if is_stale:
                entry = _stale_entry(dag_id, run, paused=paused, now=current)
                stale_runs = stale.setdefault(workspace, []) if workspace else unscoped_stale
                stale_runs.append(entry)
            elif not workspace:
                unscoped = True
            else:
                running.add(workspace)
    return {
        "running": running,
        "stale": stale,
        "unscoped": unscoped,
        "unscoped_stale": unscoped_stale,
    }


def daily_conf(target: Mapping[str, str], *, day: str, run_id: str) -> dict[str, Any]:
    return {
        "target": "all",
        "mode": "incremental",
        "cartridge_id": CARTRIDGE_ID,
        "tenant_id": target["tenant_id"],
        "workspace_id": target["workspace_id"],
        "conn_id": target["conn_id"],
        "triggered_by": DAILY_DAG_ID,
        "idempotency_key": f"sf-daily:{day}:{target['workspace_id']}",
        "security_context": build_scheduled_run_context(
            cartridge_id=CARTRIDGE_ID,
            tenant_id=target["tenant_id"],
            workspace_id=target["workspace_id"],
            dag_id=EXTRACT_ALL_DAG_ID,
            dag_run_id=run_id,
            issuer=DAILY_DAG_ID,
        ),
    }


def reservation_args(
    target: Mapping[str, str], *, run_id: str, conf: Mapping[str, Any], now: datetime
) -> dict[str, Any]:
    return {
        "dag_id": EXTRACT_ALL_DAG_ID,
        "cartridge_id": CARTRIDGE_ID,
        "entity": AGGREGATE_ENTITY,
        "run_id": run_id,
        "airflow_dag_run_id": run_id,
        "mode": "incremental",
        "status": "queued",
        "started_at": now.isoformat(),
        "tenant_id": target["tenant_id"],
        "workspace_id": target["workspace_id"],
        "extra": {
            "triggered_by": DAILY_DAG_ID,
            "idempotency_key": conf.get("idempotency_key"),
            "target": "all",
            "conn_id": target["conn_id"],
            "reserved": True,
        },
    }


def reserve_extract_all(
    target: Mapping[str, str],
    *,
    run_id: str,
    conf: Mapping[str, Any],
    mcp_url: str,
    headers: Mapping[str, str],
    http: Any = requests,
    now: datetime | None = None,
) -> None:
    args = reservation_args(
        target, run_id=run_id, conf=conf, now=_aware(now) or datetime.now(timezone.utc)
    )
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
    payload = response.json() if 200 <= int(response.status_code) < 300 else None
    result = payload.get("result") if isinstance(payload, Mapping) else None
    if not isinstance(result, Mapping) or result.get("saved") is not True:
        raise RuntimeError("extract_all reservation was not recorded")


def trigger_targets(
    targets: Sequence[Mapping[str, str]],
    *,
    day: str,
    base_url: str,
    username: str,
    password: str,
    http: Any = requests,
    reserve: Callable[..., None] | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    if not targets:
        return []
    try:
        active = active_extraction_workspaces(
            base_url=base_url, username=username, password=password, http=http, now=now
        )
    except AirflowTriggerError as exc:
        return [
            {
                "workspace_id": target["workspace_id"],
                "outcome": "failed",
                "stage": exc.stage,
                "status": exc.status_code,
            }
            for target in targets
        ]
    results: list[dict[str, Any]] = []
    for target in targets:
        workspace = target["workspace_id"]
        run_id = daily_run_id(day, workspace)
        if active["unscoped"]:
            results.append(
                {"workspace_id": workspace, "outcome": "failed", "stage": "unscoped_active_run"}
            )
            continue
        stale_runs = [*active["stale"].get(workspace, []), *active["unscoped_stale"]]
        if stale_runs:
            results.append(
                {
                    "workspace_id": workspace,
                    "outcome": "blocked",
                    "stage": "stale_active_run",
                    "stale_runs": stale_runs[:_STALE_RUNS_REPORTED],
                }
            )
            continue
        if workspace in active["running"]:
            results.append({"workspace_id": workspace, "outcome": "already_running"})
            continue
        conf = daily_conf(target, day=day, run_id=run_id)
        try:
            status_code = trigger_dag_run(
                base_url=base_url,
                dag_id=EXTRACT_ALL_DAG_ID,
                run_id=run_id,
                conf=conf,
                username=username,
                password=password,
                http=http,
            )
        except AirflowTriggerError as exc:
            results.append(
                {
                    "workspace_id": workspace,
                    "outcome": "failed",
                    "stage": exc.stage,
                    "status": exc.status_code,
                }
            )
            continue
        except Exception as exc:  # noqa: BLE001 - isolate workspaces, report class only
            results.append(
                {"workspace_id": workspace, "outcome": "failed", "error_type": type(exc).__name__}
            )
            continue
        if status_code == 409:
            results.append(
                {"workspace_id": workspace, "outcome": "duplicate", "dag_run_id": run_id}
            )
            continue
        result: dict[str, Any] = {
            "workspace_id": workspace,
            "outcome": "triggered",
            "dag_run_id": run_id,
        }
        if reserve is not None:
            try:
                reserve(target, run_id=run_id, conf=conf)
                result["reserved"] = True
            except Exception:  # noqa: BLE001 - the run is already queued; report it
                result["reserved"] = False
        results.append(result)
    return results


def run_outcome(
    discovery: Mapping[str, Any] | None, results: Sequence[Mapping[str, Any]]
) -> tuple[str, str | None]:
    if not isinstance(discovery, Mapping):
        return "failed", "discovery_failed"
    if discovery.get("status") == "late_catchup":
        return "skipped", "late_catchup"
    expected = len(discovery.get("targets") or [])
    reported_failures = sum(1 for item in results if item.get("outcome") == "failed")
    blocked = sum(1 for item in results if item.get("outcome") == "blocked")
    ok = len(results) - reported_failures - blocked
    failed = reported_failures + max(0, expected - len(results))
    if failed:
        return ("partial" if ok else "failed"), "workspaces_failed"
    if blocked:
        return ("partial" if ok else "blocked"), "stale_active_run"
    if discovery.get("status") == "partial" or int(discovery.get("scope_failures") or 0):
        return "partial", "scope_discovery_partial"
    if any(item.get("reserved") is False for item in results):
        return "partial", "reservation_failed"
    if not results:
        return "skipped", "no_successfactors_connection"
    if not any(item.get("outcome") in {"triggered", "duplicate"} for item in results):
        return "skipped", "already_running"
    return "success", None
