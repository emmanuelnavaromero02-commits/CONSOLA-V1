from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Callable


SYNC_STEP_RECOMPUTE_PERCENT_STATUSES = {
    "success",
    "partial",
    "blocked",
    "failed",
    "skipped",
    "skipped_explicit",
}
SYNC_CHILD_BLOCKED_STATUSES = {"blocked", "skipped", "skipped_explicit"}
SYNC_STEP_ORDER = (
    "connection",
    "bronze",
    "silver_gold",
    "control_room",
    "agents_intelligence",
)


def sync_step(
    step_id: str,
    label: str,
    status: str,
    detail: str = "",
    *,
    attempts: int = 0,
    error: str | None = None,
    completed: int | None = None,
    total: int | None = None,
    percent: int | None = None,
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_total = max(int(total or 0), 0)
    normalized_completed = max(int(completed or 0), 0)
    if normalized_total:
        normalized_completed = min(normalized_completed, normalized_total)
    normalized_percent = (
        int(percent)
        if percent is not None
        else round((normalized_completed / normalized_total) * 100)
        if normalized_total
        else 100
        if status in {"success", "skipped"}
        else 0
    )
    return {
        "id": step_id,
        "label": label,
        "status": status,
        "detail": detail,
        "attempts": attempts,
        "completed": normalized_completed,
        "total": normalized_total,
        "percent": max(0, min(normalized_percent, 100)),
        **({"error": error} if error else {}),
        **({"metrics": metrics} if metrics else {}),
    }


def normalize_sync_step_payload(
    step: dict[str, Any],
    *,
    recompute_statuses: set[str] | None = None,
) -> dict[str, Any]:
    recompute_statuses = recompute_statuses or SYNC_STEP_RECOMPUTE_PERCENT_STATUSES
    normalized = dict(step)
    status = str(normalized.get("status") or "queued")
    total = max(int(normalized.get("total") or 0), 0)
    completed = max(int(normalized.get("completed") or 0), 0)
    if status in {"success", "skipped", "skipped_explicit"} and not total:
        total = 1
        completed = 1
    if status in {"failed", "blocked"} and not total:
        total = 1
    if total:
        completed = min(completed, total)
    if total and status in recompute_statuses:
        percent = round((completed / total) * 100)
    elif "percent" in normalized and normalized.get("percent") is not None:
        percent = int(normalized.get("percent") or 0)
    elif total:
        percent = round((completed / total) * 100)
    elif status in {"success", "skipped", "skipped_explicit"}:
        percent = 100
    elif status == "running":
        percent = 50
    else:
        percent = 0
    normalized["completed"] = completed
    normalized["total"] = total
    normalized["percent"] = max(0, min(percent, 100))
    return normalized


def initial_sync_steps() -> list[dict[str, Any]]:
    return [
        sync_step("connection", "Conexión", "queued", "Esperando preflight de extracción."),
        sync_step("bronze", "Bronze", "queued", "Extracción pendiente."),
        sync_step("silver_gold", "Silver/Gold", "queued", "Materialización pendiente."),
        sync_step("control_room", "Control Room", "queued", "Refresh pendiente."),
        sync_step(
            "agents_intelligence",
            "Agentes/IA",
            "queued",
            "Monitores y simulaciones pendientes.",
        ),
    ]


def merge_sync_steps(
    current: list[dict[str, Any]] | None,
    updates: dict[str, dict[str, Any]],
    *,
    step_order: tuple[str, ...] = SYNC_STEP_ORDER,
    normalizer: Callable[[dict[str, Any]], dict[str, Any]] = normalize_sync_step_payload,
    initial_steps_factory: Callable[[], list[dict[str, Any]]] = initial_sync_steps,
) -> list[dict[str, Any]]:
    base = {
        str(step.get("id")): dict(step)
        for step in (current or initial_steps_factory())
        if isinstance(step, dict) and step.get("id")
    }
    for step_id, update in updates.items():
        merged = {**base.get(step_id, {}), **update, "id": step_id}
        base[step_id] = normalizer(merged)
    return [normalizer(base[item]) for item in step_order if item in base]


def sync_status_from_steps(steps: list[dict[str, Any]]) -> str:
    statuses = {str(step.get("status") or "") for step in steps}
    if "failed" in statuses:
        return "failed"
    if "running" in statuses or "queued" in statuses:
        return "running"
    if statuses & {"partial", "blocked", "skipped_explicit"}:
        return "partial"
    return "success"


def sync_extra_from_row(row: dict[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {}
    raw = row.get("extra")
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def sync_child_reason(row: dict[str, Any]) -> str | None:
    extra = sync_extra_from_row(row)
    for key in (
        "reason",
        "blocked_reason",
        "metadata_status",
        "status_reason",
        "error_code",
    ):
        value = extra.get(key)
        if value:
            return str(value)[:240]
    error_message = row.get("error_message")
    if error_message:
        return str(error_message)[:240]
    raw_conf = extra.get("raw_conf") if isinstance(extra.get("raw_conf"), dict) else {}
    reason = raw_conf.get("reason") or raw_conf.get("metadata_status")
    return str(reason)[:240] if reason else None


def sync_entity_idempotency_key(base_key: object | None, entity: object | None) -> str | None:
    if base_key is None:
        return None
    base = str(base_key).strip()
    if not base:
        return None
    entity_name = str(entity or "").strip() or "entity"
    candidate = f"{base}:{entity_name}"
    if len(candidate) <= 160:
        return candidate
    digest = uuid.uuid5(uuid.NAMESPACE_URL, candidate).hex
    return f"{base[:100]}:{digest}"


def sync_now_run_id_from_request_id(
    *, cartridge: str, request_id: str | None, lock_key: str
) -> str:
    if not request_id:
        return f"sync_now:{cartridge}:{uuid.uuid4().hex}"
    digest = uuid.uuid5(uuid.NAMESPACE_URL, f"{lock_key}:{request_id}").hex
    return f"sync_now:{cartridge}:{digest}"


def sync_run_age_seconds(row: dict[str, Any], *, now: datetime | None = None) -> float | None:
    started_at = row.get("started_at")
    if isinstance(started_at, str):
        try:
            normalized = started_at.replace("Z", "+00:00")
            started_at = datetime.fromisoformat(normalized)
        except ValueError:
            return None
    if not started_at:
        return None
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    return max((now - started_at).total_seconds(), 0.0)


def sync_step_entity_summary(
    rows: list[dict[str, Any]],
    *,
    aggregate_entity: str,
    sync_now_entity: str,
    blocked_statuses: set[str] = SYNC_CHILD_BLOCKED_STATUSES,
) -> dict[str, Any]:
    entities: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    for row in rows:
        entity = str(row.get("entity") or "").strip()
        if not entity or entity in {aggregate_entity, sync_now_entity}:
            continue
        status = str(row.get("status") or "unknown").strip().lower() or "unknown"
        extra = sync_extra_from_row(row)
        reason = sync_child_reason(row)
        item = {
            "entity": entity,
            "status": status,
            "row_count": int(row.get("row_count") or 0),
            **({"reason": reason} if reason else {}),
            **(
                {"metadata_status": str(extra.get("metadata_status"))[:120]}
                if extra.get("metadata_status")
                else {}
            ),
            **(
                {"fields_missing": extra.get("fields_missing")}
                if isinstance(extra.get("fields_missing"), list)
                else {}
            ),
            **(
                {"fields_used": extra.get("fields_used")}
                if isinstance(extra.get("fields_used"), list)
                else {}
            ),
        }
        entities.append(item)
        if status in blocked_statuses or status in {"failed", "error"}:
            blockers.append(item)
    return {
        "entities": entities,
        "blockers": blockers,
        "counts": {
            "success": sum(1 for item in entities if item["status"] == "success"),
            "partial": sum(1 for item in entities if item["status"] == "partial"),
            "blocked": sum(1 for item in entities if item["status"] in blocked_statuses),
            "failed": sum(
                1 for item in entities if item["status"] in {"failed", "error"}
            ),
        },
    }


def public_sync_payload(
    row: dict[str, Any],
    extra: dict[str, Any],
    *,
    terminal_statuses: set[str],
    normalizer: Callable[[dict[str, Any]], dict[str, Any]] = normalize_sync_step_payload,
    initial_steps_factory: Callable[[], list[dict[str, Any]]] = initial_sync_steps,
) -> dict[str, Any]:
    steps = (
        extra.get("steps")
        if isinstance(extra.get("steps"), list)
        else initial_steps_factory()
    )
    steps = [normalizer(step) for step in steps if isinstance(step, dict)]
    status = str(row.get("status") or extra.get("status") or "running")
    step_count = len(steps)
    progress = (
        round(
            sum(
                max(0, min(int(step.get("percent") or 0), 100))
                for step in steps
                if isinstance(step, dict)
            )
            / max(step_count, 1)
        )
        if step_count
        else 0
    )
    return {
        "run_id": row.get("run_id"),
        "cartridge_id": row.get("cartridge_id"),
        "status": status,
        "active": status.lower() not in terminal_statuses,
        "mode": row.get("mode") or extra.get("mode") or "incremental",
        "target": extra.get("target") or "all",
        "progress_percent": max(0, min(progress, 100)),
        "steps": steps,
        "triggered_entities": extra.get("triggered_entities") or [],
        "errors": extra.get("errors") or [],
        "control_room_ready": bool(extra.get("control_room_ready")),
        "control_room_snapshot": extra.get("control_room_snapshot") or {},
        "agentops_refresh": extra.get("agentops_refresh") or {},
        "started_at": row.get("started_at").isoformat()
        if row.get("started_at")
        else None,
        "finished_at": row.get("finished_at").isoformat()
        if row.get("finished_at")
        else None,
        "error_message": row.get("error_message"),
    }


def inactive_sync_run_payload(
    *, cartridge: str, mode: str, target: str, conn_id: str | None = None
) -> dict[str, Any]:
    return {
        "run_id": None,
        "cartridge_id": cartridge,
        "status": "skipped",
        "active": False,
        "mode": mode,
        "target": target,
        "progress_percent": 0,
        "steps": [],
        "triggered_entities": [],
        "errors": [],
        "control_room_ready": False,
        "control_room_snapshot": {},
        "agentops_refresh": {},
        "started_at": None,
        "finished_at": None,
        "error_message": None,
        "reason": "no_active_sync_run",
        "conn_id": conn_id,
    }


def extract_all_public_response(result: dict[str, Any]) -> dict[str, Any]:
    triggered = list(result.get("triggered") or [])
    errors = list(result.get("errors") or [])
    partial = list(result.get("partial") or [])
    skipped_explicit = list(result.get("skipped_explicit") or [])
    blocked = [
        item for item in errors
        if item.get("status_code") in {400, 403, 404}
    ]
    failed = [
        item for item in errors
        if item.get("status_code") not in {400, 403, 404}
    ]
    attempted = len(triggered) + len(errors) + len(partial) + len(skipped_explicit)
    return {
        **result,
        "attempted": attempted,
        "triggered": triggered,
        "errors": errors,
        "blocked": blocked,
        "failed": failed,
        "partial": partial,
        "skipped_explicit": skipped_explicit,
        "summary": {
            "attempted": attempted,
            "triggered": len(triggered),
            "errors": len(errors),
            "blocked": len(blocked),
            "failed": len(failed),
            "partial": len(partial),
            "skipped_explicit": len(skipped_explicit),
        },
        "count": len(triggered),
        "error_count": len(errors),
    }


def child_gold_refresh_summary(child_rows: list[dict[str, Any]]) -> dict[str, Any]:
    materialized = 0
    total = 0
    results: list[dict[str, Any]] = []
    statuses: list[str] = []
    for row in child_rows:
        extra = sync_extra_from_row(row)
        payload = extra.get("gold_refresh")
        if not isinstance(payload, dict):
            continue
        status = str(payload.get("status") or "").strip().lower()
        if status:
            statuses.append(status)
        payload_results = [
            item for item in (payload.get("results") or []) if isinstance(item, dict)
        ]
        results.extend(payload_results)
        payload_total = payload.get("total")
        if isinstance(payload_total, int):
            total += payload_total
        elif payload_results:
            total += len(payload_results)
        payload_materialized = payload.get("materialized")
        if isinstance(payload_materialized, int):
            materialized += payload_materialized
        elif payload_results:
            materialized += sum(
                1 for item in payload_results if item.get("status") == "ok"
            )
    if not total and not results:
        return {}
    status = (
        "success"
        if total and materialized >= total
        else "partial"
        if materialized
        else "failed"
    )
    if "failed" in statuses and not materialized:
        status = "failed"
    elif "partial" in statuses and status == "success":
        status = "partial"
    return {
        "status": status,
        "materialized": materialized,
        "total": total or len(results),
        "failed": max((total or len(results)) - materialized, 0),
        "results": results,
    }


def gold_refresh_dataset_names(gold_refresh_summary: dict[str, Any]) -> list[str]:
    names: set[str] = set()
    for item in gold_refresh_summary.get("results") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("status") or "").strip().lower() != "ok":
            continue
        name = str(item.get("name") or "").strip()
        if name:
            names.add(name)
    return sorted(names)


def control_room_gold_refresh_terminal(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    status = str(payload.get("status") or "").strip().lower()
    return status in {
        "success",
        "partial",
        "failed",
        "skipped",
        "not_ready",
        "completed",
    }
