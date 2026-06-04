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
        intelligence_where, intelligence_args = _scoped_where(user)
        intelligence_open = await _safe_fetchval(conn,
            f"""
            SELECT COUNT(*) FROM intelligence_signals
            WHERE status = 'open'
            {intelligence_where}
            """,
            *intelligence_args,
        )
        intelligence_high_open = await _safe_fetchval(conn,
            f"""
            SELECT COUNT(*) FROM intelligence_signals
            WHERE status = 'open'
              AND severity IN ('critical', 'high')
            {intelligence_where}
            """,
            *intelligence_args,
        )
        intelligence_predictive_open = await _safe_fetchval(conn,
            f"""
            SELECT COUNT(*) FROM intelligence_signals
            WHERE status = 'open'
              AND (
                COALESCE(signal_subtype, '') = 'predictive'
                OR prediction_horizon_days IS NOT NULL
              )
            {intelligence_where}
            """,
            *intelligence_args,
        )
        intelligence_generated_24h = await _safe_fetchval(conn,
            f"""
            SELECT COUNT(*) FROM intelligence_signals
            WHERE created_at >= now() - INTERVAL '24 hours'
            {intelligence_where}
            """,
            *intelligence_args,
        )
        intelligence_outcomes_24h = await _safe_fetchval(conn,
            f"""
            SELECT COUNT(*) FROM prediction_outcomes
            WHERE created_at >= now() - INTERVAL '24 hours'
            {intelligence_where}
            """,
            *intelligence_args,
        )
        intelligence_options_selected_24h = await _safe_fetchval(conn,
            f"""
            SELECT COUNT(*) FROM decision_options
            WHERE selected = TRUE
              AND updated_at >= now() - INTERVAL '24 hours'
            {intelligence_where}
            """,
            *intelligence_args,
        )
        intelligence_external_errors_24h = await _safe_fetchval(conn,
            f"""
            SELECT COUNT(*) FROM external_intelligence_sources
            WHERE last_run_at >= now() - INTERVAL '24 hours'
              AND COALESCE(last_status, '') IN ('error', 'failed', 'failure', 'unavailable')
            {intelligence_where}
            """,
            *intelligence_args,
        )
        intelligence_external_cache_active = await _safe_fetchval(conn,
            f"""
            SELECT COUNT(*) FROM external_evidence_cache
            WHERE expires_at > now()
            {intelligence_where}
            """,
            *intelligence_args,
        )
        intelligence_run_count_24h = await _safe_fetchval(conn,
            f"""
            SELECT COUNT(*) FROM audit_events a
            WHERE a.created_at >= now() - INTERVAL '24 hours'
              AND a.action = 'intelligence.run'
            {audit_where}
            """,
            *audit_args,
        )
        intelligence_run_errors_24h = await _safe_fetchval(conn,
            f"""
            SELECT COUNT(*) FROM audit_events a
            WHERE a.created_at >= now() - INTERVAL '24 hours'
              AND a.action LIKE 'intelligence.%'
              AND COALESCE(a.status, '') IN ('error', 'failure', 'failed')
            {audit_where}
            """,
            *audit_args,
        )
        intelligence_avg_run_ms = await _safe_fetchval(conn,
            f"""
            SELECT AVG((a.metadata->>'duration_ms')::numeric)
            FROM audit_events a
            WHERE a.created_at >= now() - INTERVAL '24 hours'
              AND a.action = 'intelligence.run'
              AND COALESCE(a.metadata->>'duration_ms', '') ~ '^[0-9]+$'
            {audit_where}
            """,
            *audit_args,
        )
        intelligence_avg_signals_per_run = await _safe_fetchval(conn,
            f"""
            SELECT AVG((a.metadata->>'signals')::numeric)
            FROM audit_events a
            WHERE a.created_at >= now() - INTERVAL '24 hours'
              AND a.action = 'intelligence.run'
              AND COALESCE(a.metadata->>'signals', '') ~ '^[0-9]+$'
            {audit_where}
            """,
            *audit_args,
        )
        intelligence_measured_outcomes_30d = await _safe_fetchval(conn,
            f"""
            SELECT COUNT(*) FROM prediction_outcomes
            WHERE created_at >= now() - INTERVAL '30 days'
              AND predicted_value IS NOT NULL
              AND actual_value IS NOT NULL
            {intelligence_where}
            """,
            *intelligence_args,
        )
        intelligence_accurate_outcomes_30d = await _safe_fetchval(conn,
            f"""
            SELECT COUNT(*) FROM prediction_outcomes
            WHERE created_at >= now() - INTERVAL '30 days'
              AND predicted_value IS NOT NULL
              AND actual_value IS NOT NULL
              AND ABS(COALESCE(prediction_error, actual_value - predicted_value))
                    <= GREATEST(ABS(predicted_value) * 0.20, 1)
            {intelligence_where}
            """,
            *intelligence_args,
        )
        measured = _int(intelligence_measured_outcomes_30d)
        accurate = _int(intelligence_accurate_outcomes_30d)
        intelligence_accuracy_rate_30d = round(accurate / measured, 4) if measured else None
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
            "intelligence": {
                "open_signals": _int(intelligence_open),
                "high_severity_open_signals": _int(intelligence_high_open),
                "predictive_open_signals": _int(intelligence_predictive_open),
                "signals_generated_24h": _int(intelligence_generated_24h),
                "run_count_24h": _int(intelligence_run_count_24h),
                "run_errors_24h": _int(intelligence_run_errors_24h),
                "avg_run_duration_ms_24h": _float(intelligence_avg_run_ms),
                "avg_signals_per_run_24h": _float(intelligence_avg_signals_per_run),
                "outcomes_recorded_24h": _int(intelligence_outcomes_24h),
                "options_selected_24h": _int(intelligence_options_selected_24h),
                "measured_outcomes_30d": measured,
                "accurate_outcomes_30d": accurate,
                "accuracy_rate_30d": intelligence_accuracy_rate_30d,
                "external_source_errors_24h": _int(intelligence_external_errors_24h),
                "external_cache_active_items": _int(intelligence_external_cache_active),
            },
            "backup": _backup_status(),
        }
