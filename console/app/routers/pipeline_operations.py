from __future__ import annotations

from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from app.domains.data_platform.source_visibility import is_security_admin_context
from app.domains.pipeline import stuck_run_recovery_service as recovery_service
from app.domains.pipeline import trigger_preflight
from app.domains.studio.access import cartridge_visible_for_context
from app.schemas.pipeline_operations import (
    StuckRunRecoveryCounts,
    StuckRunRecoveryRequest,
    StuckRunRecoveryResponse,
    StuckRunRecoveryRun,
)
from app.services import request_rate_limits
from app.services.csrf import require_csrf
from app.services.permissions import require_permission
from app.services.runtime_calls import runtime_user
from app.services.security_context import build_security_context


router = APIRouter(tags=["pipelines"])

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


__all__ = ("recover_stuck_pipeline_runs", "router")
