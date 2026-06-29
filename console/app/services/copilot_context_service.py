"""Live console context for Copilot recommendations.

The proactive briefing is user-scoped and request-time. This module keeps a
workspace-scoped operational cut that can refresh on a schedule, dedupe
recommendations, and feed the Copilot with current console context.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException

from app.services import auth, control_room_service, proactive_service
from app.services._copilot_helpers import has_table_cached
from app.services.db_scope import scoped_db, scoped_db_for_user, workspace_scope_from_user


logger = logging.getLogger(__name__)


SNAPSHOT_TABLE = "copilot_context_snapshots"
RECOMMENDATIONS_TABLE = "copilot_recommendations"
DEFAULT_INTERVAL_SECONDS = 3600
DEFAULT_WORKSPACE_LIMIT = 200


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
    try:
        return int(value or 0)
    except (TypeError, ValueError):
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


def build_recommendations_from_snapshot(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
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
            recommendations.append(_recommendation(
                fingerprint=_fingerprint("control_room", "open_pressure", critical, high),
                severity="critical" if critical else "warning",
                category="control_room",
                title="Revisar señales prioritarias",
                body=(
                    f"Hay {critical} críticas y {high} altas abiertas en Control Room. "
                    "Conviene revisar responsables, evidencia y siguiente acción."
                ),
                evidence={"open_items_by_severity": open_by_severity, "open_total": total_open},
                action_label="Abrir Control Room",
                action_href="/control-room",
                required_permission="monitor.read",
            ))

    agents_source = sources.get("control_room.agents_ops")
    agents = agents_source.get("data") if isinstance(agents_source, dict) else None
    if isinstance(agents, dict):
        summary = agents.get("summary") or {}
        monitor_agents = _int(summary.get("monitor_agents"))
        active_agents = _int(summary.get("active_agents"))
        recent_runs = _int(summary.get("recent_runs"))
        failed_recent = _int(summary.get("failed_recent_runs"))
        if monitor_agents == 0:
            recommendations.append(_recommendation(
                fingerprint=_fingerprint("agentops", "missing_monitors"),
                severity="warning",
                category="agentops",
                title="Activar monitores operativos",
                body="No hay monitores activos para convertir señales en seguimiento operativo.",
                evidence={"summary": summary},
                action_label="Ver agentes",
                action_href="/agents",
                required_permission="agents.read",
            ))
        elif active_agents and recent_runs == 0:
            recommendations.append(_recommendation(
                fingerprint=_fingerprint("agentops", "no_recent_runs", monitor_agents),
                severity="info",
                category="agentops",
                title="Ejecutar revisión de agentes",
                body="Los monitores están configurados, pero todavía no tienen corridas recientes.",
                evidence={"summary": summary},
                action_label="Ver AgentOps",
                action_href="/viewer?type=jobs",
                required_permission="monitor.read",
            ))
        if failed_recent:
            recommendations.append(_recommendation(
                fingerprint=_fingerprint("agentops", "failed_runs", failed_recent),
                severity="warning",
                category="agentops",
                title="Revisar corridas de agentes fallidas",
                body=f"Hay {failed_recent} corridas recientes de agentes con error.",
                evidence={"summary": summary},
                action_label="Abrir Jobs",
                action_href="/viewer?type=jobs",
                required_permission="monitor.read",
            ))

    talent = sources.get("control_room.sap_successfactors_talent_kpis", {}).get("data") or {}
    if isinstance(talent, dict):
        readiness = talent.get("readiness") or {}
        blockers = talent.get("blockers") or []
        status = str(readiness.get("status") or readiness.get("readiness_status") or "").lower()
        if blockers or status in {"partial", "blocked", "insufficient_data"}:
            recommendations.append(_recommendation(
                fingerprint=_fingerprint("sap_successfactors", "talent_blockers", status, len(blockers)),
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
            ))

    metadata = (
        sources.get("control_room.sap_successfactors_talent_metadata_readiness", {}).get("data")
        or {}
    )
    if isinstance(metadata, dict):
        summary = metadata.get("summary") or {}
        blocked = _int(summary.get("blocked_entities"))
        live_total = _int(summary.get("live_required_total"))
        live_ready = _int(summary.get("live_required_ready"))
        if blocked or (live_total and live_ready < live_total):
            recommendations.append(_recommendation(
                fingerprint=_fingerprint("sap_successfactors", "metadata_readiness", blocked, live_ready, live_total),
                severity="warning",
                category="sap_successfactors",
                title="Validar metadata y permisos SuccessFactors",
                body="Hay componentes de Talento que dependen de metadata o permisos del tenant.",
                evidence={"summary": summary, "status": metadata.get("status")},
                action_label="Ver readiness",
                action_href="/control-room?front=talent",
                required_permission="monitor.read",
            ))

    operational_source = sources.get("database.operational_counts")
    operational = operational_source.get("data") if isinstance(operational_source, dict) else None
    if isinstance(operational, dict):
        pipeline = operational.get("pipeline") or {}
        by_status = pipeline.get("by_status") or {}
        partial = _int(by_status.get("partial"))
        failed = _int(by_status.get("failed")) + _int(by_status.get("error"))
        blocked = _int(by_status.get("blocked"))
        if failed or blocked or partial:
            recommendations.append(_recommendation(
                fingerprint=_fingerprint("pipeline", "non_success", failed, blocked, partial),
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
            ))

        datasets = operational.get("datasets") or {}
        datasets_total = _int(datasets.get("total"))
        if datasets_total == 0:
            recommendations.append(_recommendation(
                fingerprint=_fingerprint("datasets", "none_registered"),
                severity="info",
                category="datasets",
                title="Registrar datasets operativos",
                body="No hay datasets registrados para este workspace; Schema y Lineage tendrán poca evidencia.",
                evidence={"datasets": datasets},
                action_label="Abrir Datasets",
                action_href="/viewer?type=datasets",
                required_permission="datasets.read",
            ))

    briefing = sources.get("copilot.proactive_briefing", {}).get("data") or {}
    highlights = briefing.get("highlights") if isinstance(briefing, dict) else []
    if isinstance(highlights, list):
        for highlight in highlights[:3]:
            if not isinstance(highlight, dict):
                continue
            recommendations.append(_recommendation(
                fingerprint=_fingerprint("briefing", highlight.get("id")),
                severity=str(highlight.get("severity") or "info"),
                category=str(highlight.get("category") or "briefing"),
                title=str(highlight.get("title") or "Recomendación del Copiloto"),
                body=str(highlight.get("body") or "Revisar señal operativa."),
                evidence={"highlight": highlight},
                action_label=highlight.get("action_label"),
                action_href=highlight.get("action_href"),
                required_permission="copilot.use",
            ))

    if not recommendations:
        recommendations.append(_recommendation(
            fingerprint=_fingerprint("console", "healthy"),
            severity="success",
            category="console",
            title="Sin acciones urgentes",
            body="No se detectaron bloqueos críticos en el corte operativo actual.",
            evidence={"snapshot_status": snapshot.get("status")},
            action_label="Ver consola",
            action_href="/control-room",
            required_permission="monitor.read",
        ))

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


async def _load_operational_counts(conn: Any, workspace_id: str, tenant_id: str | None) -> dict[str, Any]:
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
            "by_status": {str(row["status"] or "unknown"): int(row["total"] or 0) for row in status_rows},
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
) -> dict[str, Any]:
    tenant_id, workspace_id = workspace_scope_from_user(user)
    pool = await auth.pool()
    sources: list[dict[str, Any]] = []

    async def _operational_counts() -> dict[str, Any]:
        async with scoped_db_for_user(pool, user) as (conn, tenant, workspace):
            return await _load_operational_counts(conn, workspace, tenant)

    loaders = [
        ("database.operational_counts", _operational_counts),
        ("control_room.ops_summary", lambda: control_room_service.ops_summary(user)),
        ("control_room.agents_ops", lambda: control_room_service.agents_ops(user, limit=8)),
        (
            "control_room.sap_successfactors_talent_kpis",
            lambda: control_room_service.sap_successfactors_talent_kpis(user),
        ),
        (
            "control_room.sap_successfactors_talent_metadata_readiness",
            lambda: control_room_service.sap_successfactors_talent_metadata_readiness(user),
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
        if not await _tables_ready(pool):
            snapshot["persisted"] = False
            snapshot["persist_error"] = "copilot live context tables missing"
            return snapshot
        async with scoped_db(pool, tenant_id, workspace_id) as conn:
            snapshot_id = await _persist_snapshot(conn, snapshot)
            await _persist_recommendations(
                conn,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                snapshot_id=snapshot_id,
                recommendations=recommendations,
            )
            snapshot["id"] = snapshot_id
            snapshot["persisted"] = True
    return snapshot


async def _proactive_briefing_for_context(user: dict[str, Any]) -> dict[str, Any]:
    user_id = int(user.get("id") or 0)
    if user_id <= 0:
        return {"highlights": []}
    highlights = await proactive_service.briefing_for_user(user_id, limit=6, user_context=user)
    return {"highlights": highlights}


def _summary_metrics(sources: list[dict[str, Any]]) -> dict[str, Any]:
    metrics: dict[str, Any] = {"sources": {item["name"]: item.get("status") for item in sources}}
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
    return bool(
        await has_table_cached(pool, SNAPSHOT_TABLE)
        and await has_table_cached(pool, RECOMMENDATIONS_TABLE)
    )


async def _persist_snapshot(conn: Any, snapshot: dict[str, Any]) -> str:
    row = await conn.fetchrow(
        """
        INSERT INTO copilot_context_snapshots (
            tenant_id, workspace_id, status, summary, sources, metrics, errors, generated_by
        )
        VALUES ($1::uuid, $2::uuid, $3, $4::jsonb, $5::jsonb, $6::jsonb, $7::jsonb, $8)
        RETURNING id::text
        """,
        snapshot.get("tenant_id"),
        snapshot.get("workspace_id"),
        snapshot.get("status") or "partial",
        _json_dumps(snapshot.get("summary") or {}),
        _json_dumps(snapshot.get("sources") or []),
        _json_dumps(snapshot.get("metrics") or {}),
        _json_dumps(snapshot.get("errors") or []),
        snapshot.get("generated_by") or "manual",
    )
    return str(row["id"])


async def _persist_recommendations(
    conn: Any,
    *,
    tenant_id: str | None,
    workspace_id: str,
    snapshot_id: str,
    recommendations: list[dict[str, Any]],
) -> None:
    emitted: list[str] = []
    for item in recommendations:
        fp = str(item.get("fingerprint") or "").strip()
        if not fp:
            continue
        emitted.append(fp)
        await conn.execute(
            """
            INSERT INTO copilot_recommendations (
                tenant_id, workspace_id, snapshot_id, fingerprint, severity, category,
                title, body, evidence, action_label, action_href, action_kind,
                required_permission, status, first_seen_at, last_seen_at
            )
            VALUES (
                $1::uuid, $2::uuid, $3::uuid, $4, $5, $6, $7, $8, $9::jsonb,
                $10, $11, $12, $13, 'active', NOW(), NOW()
            )
            ON CONFLICT (workspace_id, fingerprint) DO UPDATE SET
                tenant_id = EXCLUDED.tenant_id,
                snapshot_id = EXCLUDED.snapshot_id,
                severity = EXCLUDED.severity,
                category = EXCLUDED.category,
                title = EXCLUDED.title,
                body = EXCLUDED.body,
                evidence = EXCLUDED.evidence,
                action_label = EXCLUDED.action_label,
                action_href = EXCLUDED.action_href,
                action_kind = EXCLUDED.action_kind,
                required_permission = EXCLUDED.required_permission,
                last_seen_at = NOW(),
                status = CASE
                    WHEN copilot_recommendations.status = 'dismissed'
                    THEN 'dismissed'
                    ELSE 'active'
                END,
                resolved_at = NULL
            """,
            tenant_id,
            workspace_id,
            snapshot_id,
            fp,
            _normalise_severity(item.get("severity")),
            str(item.get("category") or "console"),
            str(item.get("title") or "Recomendación"),
            str(item.get("body") or ""),
            _json_dumps(item.get("evidence") or {}),
            item.get("action_label"),
            item.get("action_href"),
            item.get("action_kind") or "navigate",
            item.get("required_permission"),
        )
    if emitted:
        await conn.execute(
            """
            UPDATE copilot_recommendations
               SET status = 'superseded',
                   resolved_at = NOW()
             WHERE workspace_id = $1::uuid
               AND status = 'active'
               AND NOT (fingerprint = ANY($2::text[]))
            """,
            workspace_id,
            emitted,
        )


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
    tenant_id, workspace_id = workspace_scope_from_user(user)
    pool = await auth.pool()
    if not await _tables_ready(pool):
        return {
            "available": False,
            "reason": "copilot live context tables missing",
            "recommendations": [],
        }
    limit = max(1, min(int(limit or 20), 100))
    status_filter = "" if include_dismissed else "AND status <> 'dismissed'"
    async with scoped_db(pool, tenant_id, workspace_id) as conn:
        rows = await conn.fetch(
            f"""
            SELECT id::text, snapshot_id::text, fingerprint, severity, category,
                   title, body, evidence, action_label, action_href, action_kind,
                   required_permission, status, first_seen_at, last_seen_at,
                   resolved_at, dismissed_at
              FROM copilot_recommendations
             WHERE workspace_id = $1::uuid
               AND ($2::uuid IS NULL OR tenant_id = $2::uuid)
               {status_filter}
             ORDER BY
               CASE severity
                 WHEN 'critical' THEN 0
                 WHEN 'warning' THEN 1
                 WHEN 'info' THEN 2
                 ELSE 3
               END,
               last_seen_at DESC
             LIMIT $3
            """,
            workspace_id,
            tenant_id,
            limit,
        )
    return {
        "available": True,
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "recommendations": [_row_public(row) for row in rows],
    }


async def dismiss_recommendation(user: dict[str, Any], recommendation_id: str) -> dict[str, Any]:
    tenant_id, workspace_id = workspace_scope_from_user(user)
    pool = await auth.pool()
    if not await _tables_ready(pool):
        raise HTTPException(503, "copilot live context tables missing")
    value = str(recommendation_id or "").strip()
    if not value or len(value) > 200:
        raise HTTPException(400, "invalid recommendation id")
    async with scoped_db(pool, tenant_id, workspace_id) as conn:
        row = await conn.fetchrow(
            """
            UPDATE copilot_recommendations
               SET status = 'dismissed',
                   dismissed_at = NOW()
             WHERE workspace_id = $1::uuid
               AND ($2::uuid IS NULL OR tenant_id = $2::uuid)
               AND (id::text = $3 OR fingerprint = $3)
             RETURNING id::text, fingerprint, status
            """,
            workspace_id,
            tenant_id,
            value,
        )
    if not row:
        raise HTTPException(404, "recommendation not found")
    return _row_public(row)


async def prompt_context_for_user(user: dict[str, Any], *, limit: int = 8) -> str | None:
    """Compact snapshot for LLM prompts."""

    try:
        snapshot = await latest_snapshot(user)
        recs = await list_recommendations(user, limit=limit)
    except Exception:
        logger.debug("copilot prompt live context failed", exc_info=True)
        return None
    payload = {
        "snapshot": snapshot,
        "recommendations": recs.get("recommendations", []),
    }
    text = _json_dumps(payload)
    max_len = 9000
    if len(text) > max_len:
        text = text[: max_len - 48] + "...<copilot-live-context-truncated>"
    return text


async def refresh_all_workspaces(*, limit: int = DEFAULT_WORKSPACE_LIMIT) -> dict[str, Any]:
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
            await collect_workspace_context(user, generated_by="scheduler", persist=True)
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

        if stop_event is not None:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval)
                return
            except asyncio.TimeoutError:
                continue
        await asyncio.sleep(interval)
