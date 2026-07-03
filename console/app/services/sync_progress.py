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
SYNC_AGGREGATE_READY_KEYS = {
    "summary",
    "result_status",
    "gold_refresh",
    "selected",
    "attempted",
    "outcomes",
}
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


def sync_run_needs_final_reconcile(
    row: dict[str, Any],
    extra: dict[str, Any],
    *,
    terminal_statuses: set[str],
    initial_step_count: int,
    aggregate_entity: str,
) -> bool:
    status = str(row.get("status") or "").lower()
    if status not in terminal_statuses:
        return True
    steps = extra.get("steps") if isinstance(extra.get("steps"), list) else []
    if len(steps) < initial_step_count:
        return True
    if any(
        str(step.get("status") or "").lower() in {"queued", "running", ""}
        for step in steps
        if isinstance(step, dict)
    ):
        return True
    if str(row.get("cartridge_id") or "") == "sap_successfactors":
        triggered = extra.get("triggered_entities")
        has_aggregate_child = any(
            isinstance(item, dict)
            and str(item.get("entity") or "") == aggregate_entity
            and str(item.get("dag_run_id") or item.get("job_id") or "").strip()
            for item in (triggered if isinstance(triggered, list) else [])
        )
        if status != "failed" and has_aggregate_child and not bool(
            extra.get("extract_all_summary_seen")
        ):
            return True
        return not bool(extra.get("control_room_checked_at"))
    return False


def sync_errors_retryable(errors: list[dict[str, Any]]) -> bool:
    if not errors:
        return False
    for error in errors:
        status_code = int(error.get("status_code") or 0)
        message = str(error.get("error") or "").lower()
        if status_code >= 500:
            continue
        if any(
            token in message
            for token in (
                "timeout",
                "tempor",
                "airflow trigger failed",
                "connection reset",
            )
        ):
            continue
        return False
    return True


def sync_child_progress_rows(
    child_rows: list[dict[str, Any]],
    *,
    aggregate_entity: str,
    sync_now_entity: str,
    ready_keys: set[str] | None = None,
) -> dict[str, Any]:
    ready_keys = ready_keys or SYNC_AGGREGATE_READY_KEYS
    aggregate_child_rows = [
        item
        for item in child_rows
        if str(item.get("entity") or "") == aggregate_entity
    ]
    aggregate_payload_ready = any(
        any(key in sync_extra_from_row(item) for key in ready_keys)
        for item in aggregate_child_rows
    )
    entity_child_rows = [
        item
        for item in child_rows
        if str(item.get("entity") or "") not in {aggregate_entity, sync_now_entity}
    ]
    aggregate_summary_pending = (
        bool(aggregate_child_rows) and not aggregate_payload_ready and not entity_child_rows
    )
    if aggregate_summary_pending:
        progress_rows = [
            {
                **item,
                "status": "running"
                if str(item.get("status") or "").lower() in {"success", "partial"}
                else item.get("status"),
            }
            for item in aggregate_child_rows
        ]
    else:
        progress_rows = entity_child_rows or child_rows
    return {
        "aggregate_child_rows": aggregate_child_rows,
        "aggregate_payload_ready": aggregate_payload_ready,
        "entity_child_rows": entity_child_rows,
        "aggregate_summary_pending": aggregate_summary_pending,
        "progress_rows": progress_rows,
    }


def sync_child_status_counts(
    progress_rows: list[dict[str, Any]],
    *,
    blocked_statuses: set[str] = SYNC_CHILD_BLOCKED_STATUSES,
    terminal_statuses: set[str],
) -> dict[str, Any]:
    statuses = [str(item.get("status") or "").lower() for item in progress_rows]
    return {
        "statuses": statuses,
        "running": any(status in {"queued", "running", "unknown"} for status in statuses),
        "failed": sum(1 for status in statuses if status in {"failed", "error"}),
        "success": sum(1 for status in statuses if status == "success"),
        "partial": sum(1 for status in statuses if status == "partial"),
        "blocked": sum(1 for status in statuses if status in blocked_statuses),
        "terminal": sum(1 for status in statuses if status in terminal_statuses),
    }


def sync_pipeline_materialization_summary(
    pipeline_rows: list[Any],
    gold_refresh_summary: dict[str, Any],
) -> dict[str, Any]:
    bronze_rows = [item for item in pipeline_rows if isinstance(item, dict)]
    bronze_ready = sum(
        1
        for item in bronze_rows
        if str(((item.get("bronze") or {}).get("status")) or "") in {"fresh", "stale"}
    )
    silver_nodes = [
        node
        for item in bronze_rows
        for node in (item.get("silver") or [])
        if isinstance(node, dict)
    ]
    gold_nodes = [
        node
        for item in bronze_rows
        for node in (item.get("gold") or [])
        if isinstance(node, dict)
    ]
    silver_ready = sum(
        1
        for node in silver_nodes
        if str(node.get("status") or "") in {"fresh", "stale"}
    )
    gold_ready = sum(
        1 for node in gold_nodes if str(node.get("status") or "") in {"fresh", "stale"}
    )
    gold_refresh_materialized = int(gold_refresh_summary.get("materialized") or 0)
    gold_refresh_total = int(gold_refresh_summary.get("total") or 0)
    if gold_refresh_total:
        gold_ready = gold_refresh_materialized
    elif gold_refresh_materialized > gold_ready:
        gold_ready = gold_refresh_materialized
    gold_total = max(len(gold_nodes), gold_refresh_total, gold_ready)
    gold_refresh_status = str(gold_refresh_summary.get("status") or "").lower()
    gold_partial = bool(
        (gold_refresh_total and gold_refresh_materialized < gold_refresh_total)
        or gold_refresh_status == "partial"
    )
    return {
        "bronze_rows": bronze_rows,
        "bronze_ready": bronze_ready,
        "silver_nodes": silver_nodes,
        "silver_ready": silver_ready,
        "gold_nodes": gold_nodes,
        "gold_ready": gold_ready,
        "gold_total": gold_total,
        "gold_partial": gold_partial,
    }


def sync_connection_step_update(
    *,
    triggered: list[Any],
    child_rows: list[dict[str, Any]],
    bronze_ready: int,
) -> dict[str, Any]:
    started = bool(triggered or child_rows or bronze_ready)
    return {
        "label": "Conexión",
        "status": "success" if started else "running",
        "detail": "Scope y conexión aceptados por el pipeline."
        if started
        else "Validando al iniciar extracción.",
        "completed": 1 if started else 0,
        "total": 1,
        "percent": 100 if started else 20,
    }


def sync_bronze_step_update(
    *,
    running_children: bool,
    aggregate_summary_pending: bool,
    progress_count: int,
    failed_children: int,
    success_children: int,
    partial_children: int,
    blocked_children: int,
    errors: list[dict[str, Any]],
    child_done: int,
    child_total: int,
    bronze_ready: int,
    entity_summary: dict[str, Any],
) -> dict[str, Any] | None:
    entities = entity_summary.get("entities", [])
    blockers = entity_summary.get("blockers", [])
    base = {
        "label": "Bronze",
        "entities": entities,
        "blockers": blockers,
    }
    if running_children:
        return {
            **base,
            "status": "running",
            "detail": "Esperando resumen final del DAG agregado."
            if aggregate_summary_pending
            else f"{progress_count} corridas del sync actual en curso.",
            "completed": child_done,
            "total": child_total,
        }
    if failed_children and not success_children and not partial_children:
        return {
            **base,
            "status": "failed",
            "detail": "Las extracciones fallaron antes de completar Bronze.",
            "completed": child_done or failed_children,
            "total": child_total,
        }
    if failed_children or partial_children or blocked_children or errors:
        return {
            **base,
            "status": "partial",
            "detail": (
                f"{success_children} OK; {partial_children} parciales; "
                f"{blocked_children} bloqueadas; "
                f"{failed_children + len(errors)} con error."
            ),
            "completed": max(
                success_children + partial_children + blocked_children,
                child_done,
            ),
            "total": child_total,
        }
    if success_children or bronze_ready:
        completed = bronze_ready or success_children
        return {
            **base,
            "status": "success",
            "detail": f"{completed} entidades con datos raw.",
            "completed": completed,
            "total": max(completed, 1),
            "percent": 100,
        }
    return None


def sync_silver_gold_step_update(
    *,
    bronze_status: str,
    running_children: bool,
    gold_total: int,
    silver_ready: int,
    gold_ready: int,
    failed_children: int,
    partial_children: int,
    blocked_children: int,
    gold_partial: bool,
    errors: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if bronze_status in {"queued", "running"} or running_children:
        return {
            "label": "Silver/Gold",
            "status": "queued",
            "detail": "Esperando a que Airflow termine extracción.",
            "completed": 0,
            "total": max(gold_total, silver_ready, 1),
        }
    if gold_ready:
        gold_detail = (
            f"{gold_ready}/{gold_total} Gold materializados o disponibles"
            if gold_total and gold_total != gold_ready
            else f"{gold_ready} Gold frescos o disponibles"
        )
        return {
            "label": "Silver/Gold",
            "status": "success"
            if (
                not failed_children
                and not partial_children
                and not blocked_children
                and not gold_partial
            )
            else "partial",
            "detail": f"{silver_ready} Silver · {gold_detail}.",
            "completed": gold_ready,
            "total": max(gold_total, gold_ready, 1),
        }
    if silver_ready:
        return {
            "label": "Silver/Gold",
            "status": "partial",
            "detail": f"{silver_ready} Silver disponibles; Gold todavía incompleto.",
            "completed": silver_ready,
            "total": max(silver_ready + 1, gold_total, 1),
        }
    if failed_children or errors:
        return {
            "label": "Silver/Gold",
            "status": "failed",
            "detail": "No se pudo confirmar materialización downstream.",
            "completed": 0,
            "total": max(gold_total, 1),
        }
    return None


def sync_control_room_snapshot(
    dashboard_payload: dict[str, Any] | Any,
    control_room_gold_refresh: dict[str, Any],
) -> dict[str, Any]:
    dashboard_meta = (
        dashboard_payload.get("meta") if isinstance(dashboard_payload, dict) else {}
    )
    dashboard_summary = (
        dashboard_payload.get("summary") if isinstance(dashboard_payload, dict) else {}
    )
    return {
        "source_count": int((dashboard_meta or {}).get("source_count") or 0),
        "item_count": int((dashboard_meta or {}).get("item_count") or 0),
        "total_items": int((dashboard_summary or {}).get("total_items") or 0),
        "data_ready_sources": int(
            (dashboard_summary or {}).get("data_ready_sources") or 0
        ),
        "gold_refresh_signals": int(control_room_gold_refresh.get("signals") or 0),
        "gold_refresh_status": str(control_room_gold_refresh.get("status") or ""),
    }


def sync_control_room_step_update(
    *,
    control_room_snapshot: dict[str, Any],
    control_room_ready: bool,
    control_room_publish_failed: bool,
    gold_ready: int,
) -> dict[str, Any]:
    source_count = int(control_room_snapshot.get("source_count") or 0)
    item_count = int(control_room_snapshot.get("item_count") or 0)
    total = max(source_count or item_count, 1)
    if control_room_ready:
        completed = total
        percent = 100
    else:
        completed = int(control_room_snapshot.get("data_ready_sources") or 0) or (
            source_count if item_count else 0
        )
        completed = min(completed, total)
        percent = min(95, round((completed / total) * 100))

    if control_room_ready:
        detail = (
            "KPIs Gold/Talent disponibles; Control Room materializado "
            f"({source_count} fuentes, {item_count} items)."
        )
    elif control_room_publish_failed:
        detail = "Gold materializado, pero la publicación de señales falló."
    elif gold_ready:
        detail = (
            "Control Room materializado "
            f"({source_count} fuentes, {item_count} items); "
            "Gold parcial o incompleto."
        )
    else:
        detail = "Control Room materializado con fuentes Bronze/Silver; esperando Gold."

    return {
        "label": "Control Room",
        "status": "success" if control_room_ready else "partial",
        "detail": detail,
        "completed": completed,
        "total": total,
        "percent": percent,
        "metrics": control_room_snapshot,
    }


def sync_control_room_error_step_update(error: Exception) -> dict[str, Any]:
    return {
        "label": "Control Room",
        "status": "partial",
        "detail": "Gold existe, pero Control Room aún no respondió completo.",
        "error": str(error)[:300],
        "completed": 0,
        "total": 1,
    }


def sync_control_room_waiting_step_update(*, running_children: bool) -> dict[str, Any]:
    return {
        "label": "Control Room",
        "status": "queued" if running_children else "partial",
        "detail": "Esperando Gold de SuccessFactors.",
        "completed": 0,
        "total": 1,
    }


def sync_control_room_generic_step_update(*, gold_ready: int) -> dict[str, Any]:
    return {
        "label": "Control Room",
        "status": "success" if gold_ready else "skipped",
        "detail": "Control Room específico no aplica para este cartucho."
        if not gold_ready
        else "Gold disponible para consumo.",
        "completed": 1 if gold_ready else 0,
        "total": 1,
        "percent": 100 if gold_ready else 0,
    }


def sync_agents_intelligence_step_update(
    *,
    applies: bool,
    can_run_agentops: bool,
    running_children: bool,
    agentops_refresh: dict[str, Any],
) -> dict[str, Any]:
    if not applies:
        return {
            "label": "Agentes/IA",
            "status": "skipped",
            "detail": "Monitores específicos no aplican para este cartucho.",
            "completed": 1,
            "total": 1,
            "percent": 100,
        }
    if not can_run_agentops:
        return {
            "label": "Agentes/IA",
            "status": "queued" if running_children else "partial",
            "detail": "Esperando datos materializados para ejecutar monitores reales.",
            "completed": 0,
            "total": 1,
        }
    agent_status = str(agentops_refresh.get("status") or "").lower()
    agent_total = int(agentops_refresh.get("total") or 0)
    agent_completed = int(agentops_refresh.get("completed") or 0)
    agent_failed = int(agentops_refresh.get("failed") or 0)
    if agent_status == "success":
        return {
            "label": "Agentes/IA",
            "status": "success",
            "detail": f"{agent_completed}/{max(agent_total, 1)} monitores ejecutados con AgentOps.",
            "completed": agent_completed,
            "total": max(agent_total, agent_completed, 1),
            "percent": 100,
            "metrics": agentops_refresh,
        }
    if agent_status == "failed":
        return {
            "label": "Agentes/IA",
            "status": "failed",
            "detail": str(
                agentops_refresh.get("reason") or f"{agent_failed} monitores fallaron."
            ),
            "completed": agent_completed,
            "total": max(agent_total, agent_failed, 1),
            "metrics": agentops_refresh,
        }
    reason = str(
        agentops_refresh.get("reason")
        or f"{agent_completed}/{max(agent_total, 1)} monitores ejecutados; {agent_failed} con error."
    )
    return {
        "label": "Agentes/IA",
        "status": "partial",
        "detail": reason,
        "completed": agent_completed,
        "total": max(agent_total, agent_completed + agent_failed, 1),
        "metrics": agentops_refresh,
    }


def sync_run_error_message(errors: list[dict[str, Any]]) -> str | None:
    message = "; ".join(
        str(item.get("error") or "") for item in errors[:3] if isinstance(item, dict)
    )
    return message or None


def sync_updated_extra(
    *,
    steps: list[dict[str, Any]],
    triggered: list[dict[str, Any]],
    errors: list[dict[str, Any]],
    control_room_ready: bool,
    control_room_checked_at: str | None,
    control_room_snapshot: dict[str, Any],
    agentops_refresh: dict[str, Any],
    gold_refresh_summary: dict[str, Any],
    control_room_gold_refresh: dict[str, Any],
    aggregate_payload_ready: bool,
    aggregate_summary_pending: bool,
    child_run_count: int,
    entity_child_run_count: int,
    entity_summary: dict[str, Any],
    previous_extra: dict[str, Any],
    row_mode: str | None,
) -> dict[str, Any]:
    return {
        "steps": steps,
        "triggered_entities": triggered,
        "errors": errors,
        "control_room_ready": control_room_ready,
        "control_room_checked_at": control_room_checked_at
        or previous_extra.get("control_room_checked_at"),
        "control_room_snapshot": control_room_snapshot
        or previous_extra.get("control_room_snapshot")
        or {},
        "agentops_refresh": agentops_refresh
        or previous_extra.get("agentops_refresh")
        or {},
        "gold_refresh": gold_refresh_summary
        or previous_extra.get("gold_refresh")
        or {},
        "control_room_gold_refresh": control_room_gold_refresh
        or previous_extra.get("control_room_gold_refresh")
        or {},
        "extract_all_summary_seen": aggregate_payload_ready
        or bool(previous_extra.get("extract_all_summary_seen")),
        "aggregate_summary_pending": aggregate_summary_pending,
        "child_run_count": child_run_count,
        "entity_child_run_count": entity_child_run_count,
        "entity_outcomes": entity_summary.get("entities") or [],
        "blockers": entity_summary.get("blockers") or [],
        "target": previous_extra.get("target") or "all",
        "mode": previous_extra.get("mode") or row_mode or "incremental",
    }


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
