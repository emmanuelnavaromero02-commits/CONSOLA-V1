from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.domains.data_platform.source_visibility import is_security_admin_context
from app.domains.pipeline import run_progress
from app.domains.pipeline import stuck_run_recovery_service as recovery_service
from app.domains.pipeline import trigger_preflight
from app.domains.studio.access import cartridge_visible_for_context
from app.schemas.pipeline_operations import (
    CARTRIDGE_PATTERN,
    MAX_PROGRESS_RUNS,
    RUN_ID_PATTERN,
    ExtractionProgressResponse,
    ExtractionProgressRun,
    StuckRunRecoveryCounts,
    StuckRunRecoveryRequest,
    StuckRunRecoveryResponse,
    StuckRunRecoveryRun,
)
from app.services import request_rate_limits
from app.services.csrf import require_csrf
from app.services.db_scope import SET_SCOPE_SQL
from app.services.permissions import require_permission
from app.services.runtime_calls import runtime_user
from app.services.security_context import build_security_context


logger = logging.getLogger(__name__)
router = APIRouter(tags=["pipelines"])
_RUN_ID_RE = re.compile(RUN_ID_PATTERN)
PROGRESS_ROW_LIMIT = 400

PLAN_CHANGED_MESSAGE_ES = (
    "Las corridas cambiaron desde la revisión. Revisa el nuevo plan y "
    "confírmalo de nuevo."
)


def _require_cartridge_visible(user: dict[str, Any], cartridge: str | None) -> None:
    if not cartridge:
        return
    ctx = build_security_context(user)
    if is_security_admin_context(ctx) or cartridge_visible_for_context(ctx, cartridge):
        return
    raise HTTPException(403, "cartridge not allowed")


def _visible_cartridges(user: dict[str, Any]) -> list[str] | None:
    """Cartridges whose runs the caller may see; None means every cartridge."""
    ctx = build_security_context(user)
    allowed = sorted(
        {
            str(item).strip()
            for item in (ctx.get("allowed_cartridges") or [])
            if str(item).strip()
        }
    )
    if is_security_admin_context(ctx) or "*" in allowed:
        return None
    return allowed


def _recovery_actor(user: dict[str, Any]) -> str:
    return f"user:{user.get('id') if user.get('id') is not None else 'unknown'}"


def _recovery_response(
    report: recovery_service.RecoveryReport,
) -> StuckRunRecoveryResponse:
    return StuckRunRecoveryResponse(
        mode=report.mode,
        checked_at=report.checked_at,
        threshold_minutes=report.threshold_minutes,
        plan_digest=report.plan_digest,
        counts=StuckRunRecoveryCounts(**report.counts),
        runs=[StuckRunRecoveryRun(**run) for run in report.runs],
        truncated=report.truncated,
        message_es=report.message_es,
    )


@router.post(
    "/api/pipelines/recover-stuck-runs",
    response_model=StuckRunRecoveryResponse,
    dependencies=[Depends(require_permission("pipelines.run")), Depends(require_csrf)],
)
async def recover_stuck_pipeline_runs(
    request: Request,
    body: StuckRunRecoveryRequest,
    user: dict = Depends(require_permission("pipelines.run")),
) -> StuckRunRecoveryResponse:
    """Dry-run by default; applying requires the digest of the reviewed plan."""
    user = runtime_user(user) or {}
    await request_rate_limits.rate_limit(
        request,
        "pipeline_recover",
        str(user.get("id") or user.get("email") or ""),
    )
    _require_cartridge_visible(user, body.cartridge)
    threshold_seconds = max(
        body.threshold_minutes * 60, trigger_preflight.stuck_run_threshold_seconds()
    )
    try:
        report = await recovery_service.recover_stuck_runs(
            user,
            mode=(
                recovery_service.MODE_APPLY
                if body.apply
                else recovery_service.MODE_DRY_RUN
            ),
            actor=_recovery_actor(user),
            invoke=recovery_service.default_invoke,
            get_db_pool=recovery_service.default_get_db_pool,
            refresh_dag_run_status=recovery_service.default_refresh_dag_run_status,
            cartridge=body.cartridge,
            dag_ids=[body.dag_id] if body.dag_id else None,
            threshold=timedelta(seconds=threshold_seconds),
            neutralize_airflow=True,
            expected_plan_digest=body.plan_digest if body.apply else None,
            exclude_run_ids=body.exclude_run_ids,
            visible_cartridges=_visible_cartridges(user),
            orphan_scan=True,
        )
    except recovery_service.PlanChanged as exc:
        raise HTTPException(
            409,
            detail={
                "reason": "plan_changed",
                "plan_digest": exc.plan_digest,
                "message": "The recovery plan changed; review it again.",
                "public_message": PLAN_CHANGED_MESSAGE_ES,
            },
        ) from exc
    return _recovery_response(report)


def _scope(user: dict[str, Any]) -> tuple[str, str]:
    tenant_id = str(user.get("active_tenant_id") or user.get("tenant_id") or "").strip()
    workspace_id = str(
        user.get("active_workspace_id") or user.get("workspace_id") or ""
    ).strip()
    if not tenant_id or not workspace_id:
        raise HTTPException(403, "active tenant/workspace is required")
    return tenant_id, workspace_id


async def _load_progress_rows(
    user: dict[str, Any], *, cartridge: str, run_ids: list[str]
) -> list[dict[str, Any]]:
    tenant_id, workspace_id = _scope(user)
    pool = await recovery_service.default_get_db_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(SET_SCOPE_SQL, tenant_id, workspace_id)
            rows = await conn.fetch(
                """
                SELECT *
                  FROM pipeline_runs
                 WHERE tenant_id = $1::uuid
                   AND workspace_id = $2::uuid
                   AND cartridge_id = $3
                   AND (run_id = ANY($4::text[]) OR airflow_dag_run_id = ANY($4::text[]))
                   AND entity <> '__sync_now__'
                 ORDER BY started_at ASC NULLS FIRST, run_id ASC
                 LIMIT $5
                """,
                tenant_id,
                workspace_id,
                cartridge,
                run_ids,
                PROGRESS_ROW_LIMIT,
            )
    return [dict(row) for row in rows]


async def _refresh_if_stale(
    row: dict[str, Any], user: dict[str, Any], *, now: datetime
) -> dict[str, Any]:
    if not run_progress.needs_airflow_refresh(row, now=now):
        return row
    try:
        refreshed = await recovery_service.default_refresh_dag_run_status(dict(row), user)
    except Exception:  # noqa: BLE001 - progress reads never fail on Airflow
        logger.debug("progress refresh failed for %s", row.get("run_id"), exc_info=True)
        return row
    return dict(refreshed) if isinstance(refreshed, dict) else row


@router.get(
    "/api/pipelines/extraction-progress",
    response_model=ExtractionProgressResponse,
    dependencies=[Depends(require_permission("pipelines.read"))],
)
async def extraction_progress(
    cartridge: str = Query(..., pattern=CARTRIDGE_PATTERN),
    run_id: list[str] = Query(default_factory=list),
    user: dict = Depends(require_permission("pipelines.read")),
) -> ExtractionProgressResponse:
    """Live extraction phases for up to 20 runs the caller launched."""
    user = runtime_user(user) or {}
    requested = list(dict.fromkeys(item.strip() for item in run_id if item.strip()))
    if len(requested) > MAX_PROGRESS_RUNS:
        raise HTTPException(422, f"at most {MAX_PROGRESS_RUNS} run_id values")
    if any(not _RUN_ID_RE.fullmatch(item) for item in requested):
        raise HTTPException(422, "invalid run_id")
    _require_cartridge_visible(user, cartridge)
    _scope(user)
    checked_at = datetime.now(timezone.utc)
    if not requested:
        return ExtractionProgressResponse(checked_at=checked_at, runs=[])

    rows = await _load_progress_rows(user, cartridge=cartridge, run_ids=requested)
    stalled_after = timedelta(seconds=trigger_preflight.stuck_run_threshold_seconds())
    runs: list[ExtractionProgressRun] = []
    for requested_id in requested:
        parent = run_progress.resolve_requested_run(requested_id, rows)
        if parent is None:
            continue
        children = run_progress.children_of(parent, rows)
        parent = await _refresh_if_stale(dict(parent), user, now=checked_at)
        runs.append(
            ExtractionProgressRun(
                **run_progress.build_run_progress(
                    parent,
                    children,
                    now=checked_at,
                    stalled_after=stalled_after,
                    run_id=requested_id,
                )
            )
        )
    return ExtractionProgressResponse(checked_at=checked_at, runs=runs)


__all__ = ("extraction_progress", "recover_stuck_pipeline_runs", "router")
