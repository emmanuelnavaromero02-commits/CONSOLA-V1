"""Workspace-scoped live console context for Copilot recommendations."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from collections.abc import Mapping
from datetime import UTC, datetime
from math import isfinite
from typing import Any

from fastapi import HTTPException

from app.schemas.control_room_operational_responses import (
    ControlRoomAgentsOpsResponse,
    ControlRoomOpsSummaryResponse,
)
from app.schemas.control_room_public_projection import PublicProjectionModel
from app.schemas.control_room_talent_responses import (
    ControlRoomTalentKpisResponse,
    ControlRoomTalentMetadataReadinessResponse,
    ControlRoomTalentOverviewResponse,
)
from app.services import (
    auth,
    control_room_service,
    copilot_context_authority,
    copilot_context_persistence,
    permissions,
    proactive_service,
)
from app.services.control_room.business_copy_sensitivity import (
    contains_sensitive_copy,
)
from app.services.control_room.diagnostic_redaction import redact_diagnostic_value
from app.services.db_scope import (
    scoped_db,
    scoped_db_for_user,
    workspace_scope_from_user,
)


logger = logging.getLogger(__name__)


SNAPSHOT_TABLE = copilot_context_authority.SNAPSHOT_TABLE
RECOMMENDATIONS_TABLE = copilot_context_authority.RECOMMENDATIONS_TABLE
DEFAULT_INTERVAL_SECONDS = 3600
DEFAULT_WORKSPACE_LIMIT = 200
DEFAULT_RETENTION_DAYS = copilot_context_persistence.DEFAULT_RETENTION_DAYS
_SOURCE_LABELS = {
    "database.operational_counts": ("platform_operations", "Operación de plataforma"),
    "control_room.ops_summary": (
        "control_room_operations",
        "Operación de Control Room",
    ),
    "control_room.agents_ops": ("agent_operations", "Operación de agentes"),
    "control_room.sap_successfactors_talent_kpis": (
        "talent_indicators",
        "Indicadores de Talento",
    ),
    "control_room.sap_successfactors_talent_metadata_readiness": (
        "talent_source_readiness",
        "Preparación de fuentes de Talento",
    ),
    "control_room.sap_successfactors_talent_overview": (
        "talent_overview",
        "Resumen de Talento",
    ),
    "copilot.proactive_briefing": ("copilot_briefing", "Recomendaciones del Copiloto"),
}
_CONTROL_ROOM_PROJECTIONS: dict[str, type[PublicProjectionModel]] = {
    "ops_summary": ControlRoomOpsSummaryResponse,
    "control_room.ops_summary": ControlRoomOpsSummaryResponse,
    "agents_ops": ControlRoomAgentsOpsResponse,
    "control_room.agents_ops": ControlRoomAgentsOpsResponse,
    "sap_successfactors_talent_kpis": ControlRoomTalentKpisResponse,
    "control_room.sap_successfactors_talent_kpis": ControlRoomTalentKpisResponse,
    "sap_successfactors_talent_metadata_readiness": (
        ControlRoomTalentMetadataReadinessResponse
    ),
    "control_room.sap_successfactors_talent_metadata_readiness": (
        ControlRoomTalentMetadataReadinessResponse
    ),
    "sap_successfactors_talent_overview": ControlRoomTalentOverviewResponse,
    "control_room.sap_successfactors_talent_overview": ControlRoomTalentOverviewResponse,
}


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, tuple):
        return [_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            pass
    return str(value)


def _json_dumps(value: Any) -> str:
    return json.dumps(_jsonable(value), ensure_ascii=False, default=str)


def _int(value: Any) -> int:
    if type(value) is float and not isfinite(value):
        return 0
    try:
        return int(value or 0)
    except (OverflowError, TypeError, ValueError):
        return 0


def _fingerprint(*parts: Any) -> str:
    raw = ":".join(str(part or "").strip().lower() for part in parts)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"live:{digest}"


def _safe_error(exc: Exception) -> str:
    if isinstance(exc, HTTPException):
        return str(exc.detail or f"HTTP {exc.status_code}")[:500]
    return str(exc or exc.__class__.__name__)[:500]


def _normalise_severity(value: Any) -> str:
    severity = str(value or "info").strip().lower()
    if severity in {"critical", "warning", "info", "success"}:
        return severity
    if severity in {"high", "error", "failed"}:
        return "critical"
    if severity in {"medium", "partial", "blocked"}:
        return "warning"
    return "info"


def _safe_text(value: Any, *, field: str, max_len: int = 1_000) -> str | None:
    if isinstance(value, datetime):
        value = value.isoformat()
    if not isinstance(value, str):
        return None
    raw = value.strip()
    clean = redact_diagnostic_value(raw, field=field)
    if (
        not isinstance(clean, str)
        or not clean
        or clean == "[REDACTED]"
        or contains_sensitive_copy(raw)
    ):
        return None
    return clean[:max_len]


def _safe_status(value: Any) -> str:
    status = str(value or "unavailable").strip().lower()
    if status in {"ready", "partial", "failed", "unavailable"}:
        return status
    if status in {"success", "ok", "healthy"}:
        return "ready"
    return "unavailable"


def _safe_identifier(value: Any) -> str | None:
    candidate = str(value or "").strip()
    if not candidate or len(candidate) > 200:
        return None
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789:-_")
    return candidate if all(character in allowed for character in candidate) else None


def _safe_action_href(value: Any) -> str | None:
    href = _safe_text(value, field="action_href", max_len=400)
    if not href or not href.startswith("/") or href.startswith("//"):
        return None
    return href


def project_control_room_diagnostic(name: str, value: Any) -> dict[str, Any] | None:
    """Project one raw Control Room service result through an explicit model."""

    model = _CONTROL_ROOM_PROJECTIONS.get(str(name or ""))
    if model is None:
        return None
    return model.project(value).model_dump(mode="json", exclude_none=True)


def _project_operational_counts(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, Mapping) else {}
    pipeline = source.get("pipeline")
    pipeline = pipeline if isinstance(pipeline, Mapping) else {}
    by_status = pipeline.get("by_status")
    by_status = by_status if isinstance(by_status, Mapping) else {}
    safe_counts = {
        status: _int(by_status.get(status))
        for status in (
            "pending",
            "running",
            "success",
            "partial",
            "blocked",
            "failed",
            "error",
        )
        if status in by_status
    }
    datasets = source.get("datasets")
    datasets = datasets if isinstance(datasets, Mapping) else {}
    watermarks = source.get("watermarks")
    watermark_groups = len(watermarks) if isinstance(watermarks, list) else 0
    return {
        "pipeline": {"by_status": safe_counts},
        "datasets": {"total": _int(datasets.get("total"))},
        "watermarks": {"groups": watermark_groups},
    }


def _project_recommendation(value: Any) -> dict[str, Any] | None:
    source = value if isinstance(value, Mapping) else {}
    recommendation_id = _safe_identifier(source.get("fingerprint"))
    if recommendation_id is None:
        return None
    status = str(source.get("status") or "active").strip().lower()
    if status not in {"active", "dismissed", "resolved", "superseded"}:
        status = "active"
    action_kind = str(source.get("action_kind") or "navigate").strip().lower()
    if action_kind not in {"navigate", "none"}:
        action_kind = "none"
    projected: dict[str, Any] = {
        "id": recommendation_id,
        "severity": _normalise_severity(source.get("severity")),
        "status": status,
        "action_kind": action_kind,
    }
    category = _safe_identifier(source.get("category"))
    if category:
        projected["category"] = category
    for key in ("title", "body", "action_label"):
        text = _safe_text(source.get(key), field=key)
        if text:
            projected[key] = text
    href = _safe_action_href(source.get("action_href"))
    if href:
        projected["action_href"] = href
    for key in (
        "first_seen_at",
        "last_seen_at",
        "resolved_at",
        "dismissed_at",
        "created_at",
    ):
        text = _safe_text(source.get(key), field=key, max_len=80)
        if text:
            projected[key] = text
    score = source.get("priority_score")
    if type(score) in {int, float}:
        try:
            numeric_score = float(score)
        except (OverflowError, TypeError, ValueError):
            numeric_score = float("nan")
        if isfinite(numeric_score):
            projected["priority_score"] = max(0, min(numeric_score, 100))
    return projected


def _project_source(value: Any) -> dict[str, Any] | None:
    source = value if isinstance(value, Mapping) else {}
    name = str(source.get("name") or "")
    identity = _SOURCE_LABELS.get(name)
    if identity is None:
        return None
    key, label = identity
    projected: dict[str, Any] = {
        "key": key,
        "label": label,
        "status": _safe_status(source.get("status")),
    }
    raw_data = source.get("data")
    if name == "database.operational_counts":
        projected["diagnostic"] = _project_operational_counts(raw_data)
    elif name == "copilot.proactive_briefing":
        data = raw_data if isinstance(raw_data, Mapping) else {}
        highlights = data.get("highlights")
        highlights = highlights if isinstance(highlights, list) else []
        projected["diagnostic"] = {
            "highlights": [
                item
                for raw in highlights[:6]
                if (item := _project_recommendation(raw)) is not None
            ]
        }
    else:
        diagnostic = project_control_room_diagnostic(name, raw_data)
        if diagnostic is not None:
            projected["diagnostic"] = diagnostic
    return projected


def project_operator_recommendations(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, Mapping) else {}
    recommendations = source.get("recommendations")
    recommendations = recommendations if isinstance(recommendations, list) else []
    return {
        "available": bool(source.get("available", True)),
        "recommendations": [
            item
            for raw in recommendations
            if (item := _project_recommendation(raw)) is not None
        ],
    }


def project_operator_snapshot(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, Mapping) else {}
    sources = source.get("sources")
    sources = sources if isinstance(sources, list) else []
    projected_sources = [
        item for raw in sources if (item := _project_source(raw)) is not None
    ]
    recommendations = source.get("recommendations")
    recommendations = recommendations if isinstance(recommendations, list) else []
    result: dict[str, Any] = {
        "available": bool(source.get("available", True)),
        "status": _safe_status(source.get("status")),
        "summary": {
            "sources_total": len(projected_sources),
            "sources_ready": sum(
                item.get("status") == "ready" for item in projected_sources
            ),
            "recommendations_total": len(recommendations),
        },
        "sources": projected_sources,
        "recommendations": [
            item
            for raw in recommendations
            if (item := _project_recommendation(raw)) is not None
        ],
    }
    generated_at = source.get("generated_at") or source.get("created_at")
    timestamp = _safe_text(generated_at, field="generated_at", max_len=80)
    if timestamp:
        result["generated_at"] = timestamp
        result["materialized_at"] = timestamp
    if type(source.get("persisted")) is bool:
        result["persisted"] = source["persisted"]
    return result


def project_dismissed_recommendation(value: Any) -> dict[str, Any]:
    projected = _project_recommendation(value)
    return projected or {"status": "dismissed"}


def _recommendation(
    *,
    fingerprint: str,
    severity: str,
    category: str,
    title: str,
    body: str,
    evidence: dict[str, Any] | None = None,
    action_label: str | None = None,
    action_href: str | None = None,
    action_kind: str = "navigate",
    required_permission: str | None = None,
) -> dict[str, Any]:
    return {
        "fingerprint": fingerprint,
        "severity": _normalise_severity(severity),
        "category": category,
        "title": title,
        "body": body,
        "evidence": evidence or {},
        "action_label": action_label,
        "action_href": action_href,
        "action_kind": action_kind,
        "required_permission": required_permission,
    }


def _source_lookup(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("name")): item
        for item in snapshot.get("sources", [])
        if isinstance(item, dict) and item.get("name")
    }


def build_recommendations_from_snapshot(
    snapshot: dict[str, Any],
) -> list[dict[str, Any]]:
    """Pure recommendation builder used by runtime and tests."""

    sources = _source_lookup(snapshot)
    recommendations: list[dict[str, Any]] = []

    ops = sources.get("control_room.ops_summary", {}).get("data") or {}
    if isinstance(ops, dict):
        open_by_severity = ops.get("open_items_by_severity") or {}
        critical = _int(open_by_severity.get("critical"))
        high = _int(open_by_severity.get("high"))
        total_open = sum(_int(v) for v in open_by_severity.values())
        if critical or high:
            recommendations.append(
                _recommendation(
                    fingerprint=_fingerprint(
                        "control_room", "open_pressure", critical, high
                    ),
                    severity="critical" if critical else "warning",
                    category="control_room",
                    title="Revisar señales prioritarias",
                    body=(
                        f"Hay {critical} críticas y {high} altas abiertas en Control Room. "
                        "Conviene revisar responsables, evidencia y siguiente acción."
                    ),
                    evidence={
                        "open_items_by_severity": open_by_severity,
                        "open_total": total_open,
                    },
                    action_label="Abrir Control Room",
                    action_href="/control-room",
                    required_permission="monitor.read",
                )
            )

    agents_source = sources.get("control_room.agents_ops")
    agents = agents_source.get("data") if isinstance(agents_source, dict) else None
    if isinstance(agents, dict):
        summary = agents.get("summary") or {}
        monitor_agents = _int(summary.get("monitor_agents"))
        active_agents = _int(summary.get("active_agents"))
        recent_runs = _int(summary.get("recent_runs"))
        failed_recent = _int(summary.get("failed_recent_runs"))
        if monitor_agents == 0:
            recommendations.append(
                _recommendation(
                    fingerprint=_fingerprint("agentops", "missing_monitors"),
                    severity="warning",
                    category="agentops",
                    title="Activar monitores operativos",
                    body="No hay monitores activos para convertir señales en seguimiento operativo.",
                    evidence={"summary": summary},
                    action_label="Ver agentes",
                    action_href="/agents",
                    required_permission="agents.read",
                )
            )
        elif active_agents and recent_runs == 0:
            recommendations.append(
                _recommendation(
                    fingerprint=_fingerprint(
                        "agentops", "no_recent_runs", monitor_agents
                    ),
                    severity="info",
                    category="agentops",
                    title="Ejecutar revisión de agentes",
                    body="Los monitores están configurados, pero todavía no tienen corridas recientes.",
                    evidence={"summary": summary},
                    action_label="Ver AgentOps",
                    action_href="/viewer?type=jobs",
                    required_permission="monitor.read",
                )
            )
        if failed_recent:
            recommendations.append(
                _recommendation(
                    fingerprint=_fingerprint("agentops", "failed_runs", failed_recent),
                    severity="warning",
                    category="agentops",
                    title="Revisar corridas de agentes fallidas",
                    body=f"Hay {failed_recent} corridas recientes de agentes con error.",
                    evidence={"summary": summary},
                    action_label="Abrir Jobs",
                    action_href="/viewer?type=jobs",
                    required_permission="monitor.read",
                )
            )

    talent = (
        sources.get("control_room.sap_successfactors_talent_kpis", {}).get("data") or {}
    )
    if isinstance(talent, dict):
        readiness = talent.get("readiness") or {}
        blockers = talent.get("blockers") or []
        status = str(
            readiness.get("status") or readiness.get("readiness_status") or ""
        ).lower()
        if blockers or status in {"partial", "blocked", "insufficient_data"}:
            recommendations.append(
                _recommendation(
                    fingerprint=_fingerprint(
                        "sap_successfactors", "talent_blockers", status, len(blockers)
                    ),
                    severity="warning",
                    category="sap_successfactors",
                    title="Completar datos de Talento",
                    body="Talento ya tiene datos base, pero faltan entradas para clasificar readiness, 9-box o acciones supervisadas.",
                    evidence={
                        "status": status,
                        "blocker_count": len(blockers),
                        "readiness": readiness,
                    },
                    action_label="Ver Talento",
                    action_href="/control-room?front=talent",
                    required_permission="monitor.read",
                )
            )

    metadata = (
        sources.get(
            "control_room.sap_successfactors_talent_metadata_readiness", {}
        ).get("data")
        or {}
    )
    if isinstance(metadata, dict):
        summary = metadata.get("summary") or {}
        blocked = _int(summary.get("blocked_entities"))
        live_total = _int(summary.get("live_required_total"))
        live_ready = _int(summary.get("live_required_ready"))
        if blocked or (live_total and live_ready < live_total):
            recommendations.append(
                _recommendation(
                    fingerprint=_fingerprint(
                        "sap_successfactors",
                        "metadata_readiness",
                        blocked,
                        live_ready,
                        live_total,
                    ),
                    severity="warning",
                    category="sap_successfactors",
                    title="Validar metadata y permisos SuccessFactors",
                    body="Hay componentes de Talento que dependen de metadata o permisos del tenant.",
                    evidence={"summary": summary, "status": metadata.get("status")},
                    action_label="Ver readiness",
                    action_href="/control-room?front=talent",
                    required_permission="operations.read",
                )
            )

    operational_source = sources.get("database.operational_counts")
    operational = (
        operational_source.get("data") if isinstance(operational_source, dict) else None
    )
    if isinstance(operational, dict):
        pipeline = operational.get("pipeline") or {}
        by_status = pipeline.get("by_status") or {}
        partial = _int(by_status.get("partial"))
        failed = _int(by_status.get("failed")) + _int(by_status.get("error"))
        blocked = _int(by_status.get("blocked"))
        if failed or blocked or partial:
            recommendations.append(
                _recommendation(
                    fingerprint=_fingerprint(
                        "pipeline", "non_success", failed, blocked, partial
                    ),
                    severity="critical" if failed else "warning",
                    category="pipeline",
                    title="Revisar sincronización parcial",
                    body=(
                        f"Pipeline tiene {failed} fallidas, {blocked} bloqueadas y "
                        f"{partial} parciales en el workspace."
                    ),
                    evidence={"by_status": by_status, "latest": pipeline.get("latest")},
                    action_label="Abrir Pipeline",
                    action_href="/viewer?type=pipeline",
                    required_permission="pipelines.read",
                )
            )

        datasets = operational.get("datasets") or {}
        datasets_total = _int(datasets.get("total"))
        if datasets_total == 0:
            recommendations.append(
                _recommendation(
                    fingerprint=_fingerprint("datasets", "none_registered"),
                    severity="info",
                    category="datasets",
                    title="Registrar datasets operativos",
                    body="No hay datasets registrados para este workspace; Schema y Lineage tendrán poca evidencia.",
                    evidence={"datasets": datasets},
                    action_label="Abrir Datasets",
                    action_href="/viewer?type=datasets",
                    required_permission="datasets.read",
                )
            )

    briefing = sources.get("copilot.proactive_briefing", {}).get("data") or {}
    highlights = briefing.get("highlights") if isinstance(briefing, dict) else []
    if isinstance(highlights, list):
        for highlight in highlights[:3]:
            if not isinstance(highlight, dict):
                continue
            recommendations.append(
                _recommendation(
                    fingerprint=_fingerprint("briefing", highlight.get("id")),
                    severity=str(highlight.get("severity") or "info"),
                    category=str(highlight.get("category") or "briefing"),
                    title=str(highlight.get("title") or "Recomendación del Copiloto"),
                    body=str(highlight.get("body") or "Revisar señal operativa."),
                    evidence={"highlight": highlight},
                    action_label=highlight.get("action_label"),
                    action_href=highlight.get("action_href"),
                    required_permission="copilot.use",
                )
            )

    if not recommendations:
        recommendations.append(
            _recommendation(
                fingerprint=_fingerprint("console", "healthy"),
                severity="success",
                category="console",
                title="Sin acciones urgentes",
                body="No se detectaron bloqueos críticos en el corte operativo actual.",
                evidence={"snapshot_status": snapshot.get("status")},
                action_label="Ver consola",
                action_href="/control-room",
                required_permission="monitor.read",
            )
        )

    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for item in recommendations:
        fp = str(item.get("fingerprint") or "")
        if fp in seen:
            continue
        seen.add(fp)
        deduped.append(item)
    return deduped[:12]


async def _source(name: str, loader) -> dict[str, Any]:
    started_at = datetime.now(UTC)
    try:
        data = await loader()
        return {
            "name": name,
            "status": "ready",
            "duration_ms": int((datetime.now(UTC) - started_at).total_seconds() * 1000),
            "data": _jsonable(data),
        }
    except Exception as exc:
        logger.debug("copilot live context source %s failed", name, exc_info=True)
        return {
            "name": name,
            "status": "failed",
            "duration_ms": int((datetime.now(UTC) - started_at).total_seconds() * 1000),
            "error": _safe_error(exc),
        }


async def _load_operational_counts(
    conn: Any, workspace_id: str, tenant_id: str | None
) -> dict[str, Any]:
    counts: dict[str, Any] = {}
    if await conn.fetchval("SELECT to_regclass('public.pipeline_runs')"):
        status_rows = await conn.fetch(
            """
            SELECT status, COUNT(*)::int AS total
              FROM pipeline_runs
             WHERE workspace_id = $1::uuid
               AND ($2::uuid IS NULL OR tenant_id = $2::uuid)
             GROUP BY status
            """,
            workspace_id,
            tenant_id,
        )
        latest_rows = await conn.fetch(
            """
            SELECT cartridge_id, entity, status, record_count, started_at, finished_at,
                   error_message, extra
              FROM pipeline_runs
             WHERE workspace_id = $1::uuid
               AND ($2::uuid IS NULL OR tenant_id = $2::uuid)
             ORDER BY started_at DESC NULLS LAST
             LIMIT 12
            """,
            workspace_id,
            tenant_id,
        )
        counts["pipeline"] = {
            "by_status": {
                str(row["status"] or "unknown"): int(row["total"] or 0)
                for row in status_rows
            },
            "latest": [_row_public(row) for row in latest_rows],
        }
    if await conn.fetchval("SELECT to_regclass('public.datasets')"):
        rows = await conn.fetch(
            """
            SELECT COALESCE(layer, 'unknown') AS layer,
                   COALESCE(cartridge, 'unknown') AS cartridge,
                   COUNT(*)::int AS total
              FROM datasets
             WHERE workspace_id = $1::uuid
               AND ($2::uuid IS NULL OR tenant_id = $2::uuid OR tenant_id IS NULL)
             GROUP BY layer, cartridge
             ORDER BY total DESC, layer, cartridge
             LIMIT 40
            """,
            workspace_id,
            tenant_id,
        )
        counts["datasets"] = {
            "total": sum(int(row["total"] or 0) for row in rows),
            "by_layer_cartridge": [_row_public(row) for row in rows],
        }
    if await conn.fetchval("SELECT to_regclass('public.entity_watermarks')"):
        rows = await conn.fetch(
            """
            SELECT cartridge_id,
                   COUNT(*)::int AS total,
                   MAX(updated_at) AS latest_updated_at
              FROM entity_watermarks
             WHERE workspace_id = $1::uuid
               AND ($2::uuid IS NULL OR tenant_id = $2::uuid OR tenant_id IS NULL)
             GROUP BY cartridge_id
             ORDER BY cartridge_id
             LIMIT 60
            """,
            workspace_id,
            tenant_id,
        )
        counts["watermarks"] = [_row_public(row) for row in rows]
    return counts


def _row_public(row: Any) -> dict[str, Any]:
    return {key: _jsonable(value) for key, value in dict(row).items()}


def _system_user(tenant_id: str | None, workspace_id: str) -> dict[str, Any]:
    return {
        "id": 0,
        "email": "system:copilot-context",
        "role": "admin",
        "active_tenant_id": tenant_id,
        "active_workspace_id": workspace_id,
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "allowed_cartridges": ["*"],
    }


async def collect_workspace_context(
    user: dict[str, Any],
    *,
    generated_by: str = "manual",
    persist: bool = True,
    ip: str | None = None,
    user_agent: str | None = None,
) -> dict[str, Any]:
    copilot_context_authority.require_refresh(user)
    tenant_id, workspace_id = workspace_scope_from_user(user)
    pool = await auth.pool()
    sources: list[dict[str, Any]] = []

    async def _operational_counts() -> dict[str, Any]:
        async with scoped_db_for_user(pool, user) as (conn, tenant, workspace):
            return await _load_operational_counts(conn, workspace, tenant)

    loaders = [
        ("database.operational_counts", _operational_counts),
        ("control_room.ops_summary", lambda: control_room_service.ops_summary(user)),
        (
            "control_room.agents_ops",
            lambda: control_room_service.agents_ops(user, limit=8),
        ),
        (
            "control_room.sap_successfactors_talent_kpis",
            lambda: control_room_service.sap_successfactors_talent_kpis(user),
        ),
        (
            "control_room.sap_successfactors_talent_metadata_readiness",
            lambda: control_room_service.sap_successfactors_talent_metadata_readiness(
                user
            ),
        ),
        (
            "control_room.sap_successfactors_talent_overview",
            lambda: control_room_service.sap_successfactors_talent_overview(user),
        ),
        (
            "copilot.proactive_briefing",
            lambda: _proactive_briefing_for_context(user),
        ),
    ]
    for name, loader in loaders:
        sources.append(await _source(name, loader))

    errors = [
        {"source": item.get("name"), "error": item.get("error")}
        for item in sources
        if item.get("status") == "failed"
    ]
    ready_sources = sum(1 for item in sources if item.get("status") == "ready")
    status = "ready" if not errors else "partial" if ready_sources else "failed"
    snapshot = {
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "status": status,
        "generated_at": datetime.now(UTC).isoformat(),
        "summary": {
            "sources_total": len(sources),
            "sources_ready": ready_sources,
            "recommendation_policy": "dedupe_by_fingerprint",
        },
        "sources": sources,
        "metrics": _summary_metrics(sources),
        "errors": errors,
        "generated_by": generated_by,
    }
    recommendations = build_recommendations_from_snapshot(snapshot)
    snapshot["recommendations"] = recommendations

    if persist:
        snapshot_id = await copilot_context_persistence.persist_refresh(
            pool,
            user,
            snapshot,
            recommendations,
            ip=ip,
            user_agent=user_agent,
        )
        if snapshot_id is None:
            snapshot["persisted"] = False
            snapshot["persist_error"] = "copilot live context tables missing"
            return snapshot
        snapshot["id"] = snapshot_id
        snapshot["persisted"] = True
    return snapshot


async def _proactive_briefing_for_context(user: dict[str, Any]) -> dict[str, Any]:
    user_id = int(user.get("id") or 0)
    if user_id <= 0:
        return {"highlights": []}
    highlights = await proactive_service.briefing_for_user(
        user_id, limit=6, user_context=user
    )
    return {"highlights": highlights}


def _summary_metrics(sources: list[dict[str, Any]]) -> dict[str, Any]:
    metrics: dict[str, Any] = {
        "sources": {item["name"]: item.get("status") for item in sources}
    }
    lookup = {item.get("name"): item.get("data") for item in sources}
    ops = lookup.get("control_room.ops_summary")
    if isinstance(ops, dict):
        metrics["control_room_items"] = ops.get("items")
        metrics["open_items_by_severity"] = ops.get("open_items_by_severity")
    agents = lookup.get("control_room.agents_ops")
    if isinstance(agents, dict):
        metrics["agentops"] = agents.get("summary")
    talent = lookup.get("control_room.sap_successfactors_talent_kpis")
    if isinstance(talent, dict):
        metrics["talent_readiness"] = talent.get("readiness")
    return metrics


async def _tables_ready(pool: Any) -> bool:
    return await copilot_context_authority.tables_ready(pool)


async def latest_snapshot(user: dict[str, Any]) -> dict[str, Any]:
    tenant_id, workspace_id = workspace_scope_from_user(user)
    pool = await auth.pool()
    if not await _tables_ready(pool):
        return {
            "available": False,
            "reason": "copilot live context tables missing",
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
        }
    async with scoped_db(pool, tenant_id, workspace_id) as conn:
        row = await conn.fetchrow(
            """
            SELECT id::text, tenant_id::text, workspace_id::text, status, summary,
                   sources, metrics, errors, generated_by, created_at
              FROM copilot_context_snapshots
             WHERE workspace_id = $1::uuid
               AND ($2::uuid IS NULL OR tenant_id = $2::uuid)
             ORDER BY created_at DESC
             LIMIT 1
            """,
            workspace_id,
            tenant_id,
        )
    if not row:
        return {
            "available": False,
            "reason": "no snapshot yet",
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
        }
    data = _row_public(row)
    data["available"] = True
    return data


async def list_recommendations(
    user: dict[str, Any],
    *,
    limit: int = 20,
    include_dismissed: bool = False,
) -> dict[str, Any]:
    return await copilot_context_authority.list_recommendations(
        user,
        limit=limit,
        include_dismissed=include_dismissed,
    )


async def dismiss_recommendation(
    user: dict[str, Any],
    recommendation_id: str,
    *,
    ip: str | None = None,
    user_agent: str | None = None,
) -> dict[str, Any]:
    return await copilot_context_authority.dismiss_recommendation(
        user,
        recommendation_id,
        ip=ip,
        user_agent=user_agent,
    )


async def prompt_context_for_user(
    user: dict[str, Any], *, limit: int = 8
) -> str | None:
    """Compact projected context; diagnostics require operational authority."""

    try:
        recs = project_operator_recommendations(
            await list_recommendations(user, limit=limit)
        )
        snapshot = None
        if permissions.has_permission(user, "operations.read"):
            snapshot = project_operator_snapshot(await latest_snapshot(user))
    except Exception:
        logger.debug("copilot prompt live context failed", exc_info=True)
        return None
    payload = {"recommendations": recs.get("recommendations", [])}
    if snapshot is not None:
        payload["snapshot"] = snapshot
    text = _json_dumps(payload)
    max_len = 9000
    if len(text) > max_len:
        text = text[: max_len - 48] + "...<copilot-live-context-truncated>"
    return text


async def refresh_all_workspaces(
    *, limit: int = DEFAULT_WORKSPACE_LIMIT
) -> dict[str, Any]:
    pool = await auth.pool()
    if not await _tables_ready(pool):
        return {"status": "skipped", "reason": "tables_missing", "workspaces": 0}
    rows = await pool.fetch(
        """
        SELECT w.id::text AS workspace_id, w.tenant_id::text AS tenant_id
          FROM workspaces w
         ORDER BY w.created_at ASC, w.name ASC
         LIMIT $1
        """,
        max(1, int(limit or DEFAULT_WORKSPACE_LIMIT)),
    )
    refreshed = 0
    failed = 0
    for row in rows:
        user = _system_user(row["tenant_id"], row["workspace_id"])
        try:
            await collect_workspace_context(
                user, generated_by="scheduler", persist=True
            )
            refreshed += 1
        except Exception:
            failed += 1
            logger.warning(
                "copilot live context refresh failed for workspace %s",
                row["workspace_id"],
                exc_info=True,
            )
    return {
        "status": "ok" if failed == 0 else "partial",
        "workspaces": len(rows),
        "refreshed": refreshed,
        "failed": failed,
    }


def _retention_days() -> int:
    raw = os.environ.get("COPILOT_CONTEXT_RETENTION_DAYS")
    if raw is None or not str(raw).strip():
        return DEFAULT_RETENTION_DAYS
    try:
        return int(str(raw).strip())
    except ValueError:
        return DEFAULT_RETENTION_DAYS


async def purge_expired_snapshots() -> dict[str, Any]:
    pool = await auth.pool()
    return await copilot_context_persistence.purge_expired_snapshots(
        pool, retention_days=_retention_days()
    )


async def hourly_scheduler(stop_event: asyncio.Event | None = None) -> None:
    raw_interval = os.environ.get("COPILOT_CONTEXT_REFRESH_SECONDS")
    try:
        interval = int(raw_interval or DEFAULT_INTERVAL_SECONDS)
    except ValueError:
        interval = DEFAULT_INTERVAL_SECONDS
    interval = max(300, interval)

    initial_delay = 10
    if stop_event is not None:
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=initial_delay)
            return
        except asyncio.TimeoutError:
            pass
    else:
        await asyncio.sleep(initial_delay)

    while True:
        try:
            result = await refresh_all_workspaces()
            logger.info("copilot live context refreshed: %s", result)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("copilot live context scheduler tick failed", exc_info=True)

        # Own try/except: a failed purge must never stop the refreshes, and a
        # failed refresh must never leave expired snapshots behind.
        try:
            purged = await purge_expired_snapshots()
            if purged.get("deleted") or purged.get("status") != "ok":
                logger.info("copilot live context purge: %s", purged)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("copilot live context purge failed", exc_info=True)

        if stop_event is not None:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval)
                return
            except asyncio.TimeoutError:
                continue
        await asyncio.sleep(interval)
