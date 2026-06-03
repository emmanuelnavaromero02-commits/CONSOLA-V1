"""Sprint v1.41.1 — Basic operational metrics endpoint.

Surfaces the counters a human operator wants on /operations:
- extractions last 24h (total / failed)
- average extraction duration last 24h (success only)
- slowest 5 entities by average duration over the last 7d
- audit_events last 24h (admin activity volume)

Charts are intentionally out of scope here — they ship in v1.44
alongside the design system refresh.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends

from app.services import auth
from app.services.permissions import canonical_role, require_permission


require_operations_read = require_permission("operations.read")

router = APIRouter(
    prefix="/api/metrics",
    tags=["metrics"],
    dependencies=[Depends(require_operations_read)],
)


def _int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _float(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _is_platform_admin(user: dict | None) -> bool:
    return canonical_role((user or {}).get("role")) in {"owner", "super_admin", "admin"}


def _tenant_workspace(user: dict | None) -> tuple[str | None, str | None]:
    if not user:
        return None, None
    tenant_id = str(user.get("active_tenant_id") or user.get("tenant_id") or "").strip() or None
    workspace_id = str(user.get("active_workspace_id") or user.get("workspace_id") or "").strip() or None
    return tenant_id, workspace_id


def _scoped_where(user: dict | None, table_alias: str = "", *, metadata: bool = False) -> tuple[str, tuple]:
    if _is_platform_admin(user):
        return "", ()
    tenant_id, workspace_id = _tenant_workspace(user)
    if not tenant_id or not workspace_id:
        return " AND FALSE", ()
    prefix = f"{table_alias}." if table_alias else ""
    if metadata:
        return (
            f" AND ({prefix}metadata->>'workspace_id' = $1 OR {prefix}metadata->>'tenant_id' = $2)",
            (workspace_id, tenant_id),
        )
    return f" AND {prefix}workspace_id = $1::uuid", (workspace_id,)


async def _safe_fetchval(conn, query: str, *args, default: object = 0) -> object:
    try:
        value = await conn.fetchval(query, *args)
    except Exception:
        return default
    return default if value is None else value


async def _safe_fetch(conn, query: str, *args) -> list[dict]:
    try:
        rows = await conn.fetch(query, *args)
    except Exception:
        return []
    return [dict(r) for r in rows]


def _backup_status() -> dict:
    path = os.environ.get("OMEGA_BACKUP_MANIFEST_PATH", "").strip()
    if not path:
        return {"status": "not_configured", "manifest_path": None, "age_seconds": None}
    manifest = Path(path)
    try:
        modified = datetime.fromtimestamp(manifest.stat().st_mtime, tz=UTC)
    except OSError:
        return {"status": "missing", "manifest_path": path, "age_seconds": None}
    age = max(0, int((datetime.now(UTC) - modified).total_seconds()))
    stale_after = _int(os.environ.get("OMEGA_BACKUP_STALE_AFTER_SECONDS") or 86400)
    return {
        "status": "stale" if age > stale_after else "fresh",
        "manifest_path": path,
        "age_seconds": age,
        "stale_after_seconds": stale_after,
    }


@router.get("/operational")
async def operational_metrics(user: dict = Depends(require_operations_read)) -> dict:
    pool = await auth.pool()
    async with pool.acquire() as conn:
        platform = _is_platform_admin(user)
        if platform:
            extractions_24h = await _safe_fetchval(conn,
                """
                SELECT COUNT(*) FROM extraction_runs
                WHERE started_at >= now() - INTERVAL '24 hours'
                """
            )
            errors_24h = await _safe_fetchval(conn,
                """
                SELECT COUNT(*) FROM extraction_runs
                WHERE started_at >= now() - INTERVAL '24 hours'
                  AND status = 'failed'
                """
            )
            avg_duration_sec = await _safe_fetchval(conn,
                """
                SELECT AVG(EXTRACT(EPOCH FROM (finished_at - started_at)))
                FROM extraction_runs
                WHERE started_at >= now() - INTERVAL '24 hours'
                  AND status = 'success'
                  AND finished_at IS NOT NULL
                """
            )
            slowest = await _safe_fetch(conn,
                """
                SELECT cartridge_id, entity_name,
                       AVG(EXTRACT(EPOCH FROM (finished_at - started_at))) AS avg_sec
                FROM extraction_runs
                WHERE started_at >= now() - INTERVAL '7 days'
                  AND status = 'success'
                  AND finished_at IS NOT NULL
                GROUP BY cartridge_id, entity_name
                ORDER BY avg_sec DESC NULLS LAST
                LIMIT 5
                """
            )
        else:
            extractions_24h = errors_24h = avg_duration_sec = 0
            slowest = []
        audit_where, audit_args = _scoped_where(user, "a", metadata=True)
        audit_count_24h = await _safe_fetchval(conn,
            f"""
            SELECT COUNT(*) FROM audit_events a
            WHERE a.created_at >= now() - INTERVAL '24 hours'
            {audit_where}
            """,
            *audit_args,
        )
        cr_where, cr_args = _scoped_where(user)
        action_executions_24h = await _safe_fetchval(conn,
            f"""
            SELECT COUNT(*) FROM control_room_action_executions
            WHERE created_at >= now() - INTERVAL '24 hours'
            {cr_where}
            """,
            *cr_args,
        )
        external_writebacks_24h = await _safe_fetchval(conn,
            f"""
            SELECT COUNT(*) FROM control_room_action_executions
            WHERE created_at >= now() - INTERVAL '24 hours'
              AND mode = 'execute_live'
              AND COALESCE(result->>'external_write', 'false') = 'true'
            {cr_where}
            """,
            *cr_args,
        )
        writeback_failures_24h = await _safe_fetchval(conn,
            f"""
            SELECT COUNT(*) FROM control_room_action_executions
            WHERE created_at >= now() - INTERVAL '24 hours'
              AND mode = 'execute_live'
              AND status IN ('failed', 'blocked')
            {cr_where}
            """,
            *cr_args,
        )
        if platform:
            jobs_24h = await _safe_fetchval(conn,
                """
                SELECT COUNT(*) FROM jobs
                WHERE created_at >= now() - INTERVAL '24 hours'
                """
            )
            failed_jobs_24h = await _safe_fetchval(conn,
                """
                SELECT COUNT(*) FROM jobs
                WHERE created_at >= now() - INTERVAL '24 hours'
                  AND status IN ('failed', 'error')
                """
            )
        else:
            jobs_24h = failed_jobs_24h = 0
        token_where, token_args = _scoped_where(user)
        llm_tokens_24h = await _safe_fetchval(conn,
            f"""
            SELECT COALESCE(SUM(input_tokens + output_tokens), 0)
            FROM token_usage
            WHERE ts >= now() - INTERVAL '24 hours'
            {token_where}
            """,
            *token_args,
        )
        llm_errors_24h = await _safe_fetchval(conn,
            f"""
            SELECT COUNT(*) FROM audit_events a
            WHERE a.created_at >= now() - INTERVAL '24 hours'
              AND (a.action LIKE 'copilot.%' OR a.action LIKE 'studio.%')
              AND COALESCE(a.status, '') IN ('error', 'failure', 'failed')
            {audit_where}
            """,
            *audit_args,
        )
        return {
            "extractions_24h": _int(extractions_24h),
            "errors_24h": _int(errors_24h),
            "avg_duration_seconds": _float(avg_duration_sec),
            "slowest_entities_7d": slowest,
            "audit_events_24h": _int(audit_count_24h),
            "control_room": {
                "action_executions_24h": _int(action_executions_24h),
                "external_writebacks_24h": _int(external_writebacks_24h),
                "writeback_failures_24h": _int(writeback_failures_24h),
            },
            "jobs": {
                "total_24h": _int(jobs_24h),
                "failed_24h": _int(failed_jobs_24h),
            },
            "llm": {
                "provider": os.environ.get("CHAT_LLM_PROVIDER", "anthropic") or "anthropic",
                "model": os.environ.get("CHAT_LLM_MODEL", ""),
                "scope": "platform" if platform else "workspace",
                "anthropic_configured": bool(os.environ.get("ANTHROPIC_API_KEY", "").strip()) if platform else None,
                "tokens_24h": _int(llm_tokens_24h),
                "errors_24h": _int(llm_errors_24h),
            },
            "backup": _backup_status(),
        }
