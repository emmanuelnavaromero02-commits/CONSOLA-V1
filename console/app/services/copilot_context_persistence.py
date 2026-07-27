"""Atomic persistence boundary for shared Copilot context refreshes."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from app.services import audit_service, copilot_context_authority
from app.services.db_scope import scoped_db, workspace_scope_from_user


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _json_dumps(value: Any) -> str:
    return json.dumps(_jsonable(value), ensure_ascii=False, default=str)


def _severity(value: Any) -> str:
    severity = str(value or "info").strip().lower()
    if severity in {"critical", "warning", "info", "success"}:
        return severity
    if severity in {"high", "error", "failed"}:
        return "critical"
    if severity in {"medium", "partial", "blocked"}:
        return "warning"
    return "info"


async def _insert_snapshot(conn: Any, snapshot: dict[str, Any]) -> str:
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


async def _upsert_recommendations(
    conn: Any,
    *,
    tenant_id: str | None,
    workspace_id: str,
    snapshot_id: str,
    recommendations: list[dict[str, Any]],
) -> None:
    emitted: list[str] = []
    for item in recommendations:
        fingerprint = str(item.get("fingerprint") or "").strip()
        if not fingerprint:
            continue
        emitted.append(fingerprint)
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
            fingerprint,
            _severity(item.get("severity")),
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


async def persist_refresh(
    pool: Any,
    actor: dict[str, Any],
    snapshot: dict[str, Any],
    recommendations: list[dict[str, Any]],
    *,
    ip: str | None = None,
    user_agent: str | None = None,
) -> str | None:
    """Persist refresh state and its critical audit event in one transaction."""

    if not await copilot_context_authority.tables_ready(pool):
        return None
    tenant_id, workspace_id = workspace_scope_from_user(actor)
    async with scoped_db(pool, tenant_id, workspace_id) as conn:
        snapshot_id = await _insert_snapshot(conn, snapshot)
        await _upsert_recommendations(
            conn,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            snapshot_id=snapshot_id,
            recommendations=recommendations,
        )
        await audit_service.record_event(
            connection=conn,
            critical=True,
            user_id=actor.get("id"),
            email=actor.get("email"),
            action="copilot.context.refresh",
            resource_type="workspace",
            resource_id=workspace_id,
            ip=ip,
            user_agent=user_agent,
            status="success" if snapshot.get("status") != "failed" else "error",
            metadata={
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "snapshot_id": snapshot_id,
                "generated_by": snapshot.get("generated_by") or "manual",
                "sources_ready": (snapshot.get("summary") or {}).get("sources_ready"),
                "sources_total": (snapshot.get("summary") or {}).get("sources_total"),
                "recommendations": len(recommendations),
            },
        )
    return snapshot_id
