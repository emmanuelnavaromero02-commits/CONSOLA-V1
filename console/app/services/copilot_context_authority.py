from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import HTTPException

from app.services import audit_service, auth, permissions
from app.services._copilot_helpers import has_table_cached
from app.services.db_scope import scoped_db, workspace_scope_from_user


SNAPSHOT_TABLE = "copilot_context_snapshots"
RECOMMENDATIONS_TABLE = "copilot_recommendations"


def _require(user: dict[str, Any], *required: str) -> None:
    missing = [name for name in required if not permissions.has_permission(user, name)]
    if missing:
        raise HTTPException(403, f"permission required: {missing[0]}")


def require_refresh(user: dict[str, Any]) -> None:

    _require(user, "operations.read", "control_room.write")


def require_recommendation_write(user: dict[str, Any]) -> None:

    _require(user, "control_room.write")


def require_declared_permission(user: dict[str, Any], required: Any) -> None:

    value = str(required or "").strip()
    if value:
        _require(user, value)


async def tables_ready(pool: Any) -> bool:
    return bool(
        await has_table_cached(pool, SNAPSHOT_TABLE)
        and await has_table_cached(pool, RECOMMENDATIONS_TABLE)
    )


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _row_public(row: Any) -> dict[str, Any]:
    return {key: _jsonable(value) for key, value in dict(row).items()}


async def list_recommendations(
    user: dict[str, Any],
    *,
    limit: int = 20,
    include_dismissed: bool = False,
) -> dict[str, Any]:
    tenant_id, workspace_id = workspace_scope_from_user(user)
    pool = await auth.pool()
    if not await tables_ready(pool):
        return {
            "available": False,
            "reason": "copilot live context tables missing",
            "recommendations": [],
        }
    bounded_limit = max(1, min(int(limit or 20), 100))
    status_filter = "" if include_dismissed else "AND status <> 'dismissed'"
    effective = sorted(permissions.get_effective_permissions(user))
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
               AND (
                    required_permission IS NULL
                    OR required_permission = ''
                    OR required_permission = ANY($3::text[])
               )
             ORDER BY
               CASE severity
                 WHEN 'critical' THEN 0
                 WHEN 'warning' THEN 1
                 WHEN 'info' THEN 2
                 ELSE 3
               END,
               last_seen_at DESC
             LIMIT $4
            """,
            workspace_id,
            tenant_id,
            effective,
            bounded_limit,
        )
    visible = []
    for row in rows:
        public_row = _row_public(row)
        required = str(public_row.get("required_permission") or "").strip()
        if required and not permissions.has_permission(user, required):
            continue
        visible.append(public_row)
    return {
        "available": True,
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "recommendations": visible,
    }


async def dismiss_recommendation(
    user: dict[str, Any],
    recommendation_id: str,
    *,
    ip: str | None = None,
    user_agent: str | None = None,
) -> dict[str, Any]:
    require_recommendation_write(user)
    tenant_id, workspace_id = workspace_scope_from_user(user)
    value = str(recommendation_id or "").strip()
    if not value or len(value) > 200:
        raise HTTPException(400, "invalid recommendation id")

    pool = await auth.pool()
    if not await tables_ready(pool):
        raise HTTPException(503, "copilot live context tables missing")
    effective = sorted(permissions.get_effective_permissions(user))
    async with scoped_db(pool, tenant_id, workspace_id) as conn:
        authority = await conn.fetchrow(
            """
            SELECT required_permission
              FROM copilot_recommendations
             WHERE workspace_id = $1::uuid
               AND ($2::uuid IS NULL OR tenant_id = $2::uuid)
               AND (id::text = $3 OR fingerprint = $3)
             FOR UPDATE
            """,
            workspace_id,
            tenant_id,
            value,
        )
        if not authority:
            raise HTTPException(404, "recommendation not found")
        require_declared_permission(user, authority["required_permission"])
        row = await conn.fetchrow(
            """
            UPDATE copilot_recommendations
               SET status = 'dismissed',
                   dismissed_at = NOW()
             WHERE workspace_id = $1::uuid
               AND ($2::uuid IS NULL OR tenant_id = $2::uuid)
               AND (id::text = $3 OR fingerprint = $3)
               AND (
                    required_permission IS NULL
                    OR required_permission = ''
                    OR required_permission = ANY($4::text[])
               )
             RETURNING id::text, fingerprint, status
            """,
            workspace_id,
            tenant_id,
            value,
            effective,
        )
        if not row:
            raise HTTPException(403, "recommendation permission changed")
        await audit_service.record_event(
            connection=conn,
            critical=True,
            user_id=user.get("id"),
            email=user.get("email"),
            action="copilot.recommendation.dismiss",
            resource_type="copilot_recommendation",
            resource_id=str(row["id"]),
            ip=ip,
            user_agent=user_agent,
            status="success",
            metadata={
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "fingerprint": row["fingerprint"],
                "required_permission": authority["required_permission"],
            },
        )
    return _row_public(row)
