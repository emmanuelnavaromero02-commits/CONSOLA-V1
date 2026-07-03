from __future__ import annotations

import json
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
