from __future__ import annotations

import asyncio
import os
import time
from copy import deepcopy
from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request

from app.dependencies import require_authenticated
from app.services import control_room_service
from app.services.csrf import require_csrf
from app.services.intelligence import history as intelligence_history
from app.services.permissions import require_permission
from app.services.security_context import build_security_context


router = APIRouter(prefix="/api/control-room", tags=["Control Room"])
_CONTROL_ROOM_READ_CACHE: dict[tuple[Any, ...], tuple[float, Any]] = {}
_CONTROL_ROOM_READ_CACHE_LOCKS: dict[tuple[Any, ...], asyncio.Lock] = {}


def _control_room_cache_ttl() -> float:
    raw = os.environ.get("OMEGA_CONTROL_ROOM_CACHE_TTL_SECONDS", "15")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 15.0
    return max(0.0, min(value, 300.0))


def _control_room_cache_identity(user: dict | None) -> tuple[Any, ...]:
    ctx = build_security_context(user)
    allowed = tuple(sorted(str(item).strip() for item in (ctx.get("allowed_cartridges") or []) if str(item).strip()))
    return (
        str(ctx.get("tenant_id") or (user or {}).get("active_tenant_id") or (user or {}).get("tenant_id") or "").strip(),
        str(ctx.get("workspace_id") or (user or {}).get("active_workspace_id") or (user or {}).get("workspace_id") or "").strip(),
        str(ctx.get("role") or (user or {}).get("role") or "").strip(),
        str((user or {}).get("id") or ctx.get("sub") or "").strip(),
        allowed,
    )


def _control_room_cache_key(namespace: str, user: dict | None) -> tuple[Any, ...]:
    return (namespace, _control_room_cache_identity(user))


def _control_room_cache_get(namespace: str, user: dict | None) -> Any | None:
    ttl = _control_room_cache_ttl()
    if ttl <= 0:
        return None
    key = _control_room_cache_key(namespace, user)
    cached = _CONTROL_ROOM_READ_CACHE.get(key)
    if not cached:
        return None
    expires_at, value = cached
    if expires_at <= time.monotonic():
        _CONTROL_ROOM_READ_CACHE.pop(key, None)
        return None
    return deepcopy(value)


def _control_room_cache_set(namespace: str, user: dict | None, value: Any) -> Any:
    ttl = _control_room_cache_ttl()
    if ttl > 0:
        _CONTROL_ROOM_READ_CACHE[_control_room_cache_key(namespace, user)] = (
            time.monotonic() + ttl,
            deepcopy(value),
        )
    return value


async def _control_room_cache_get_or_set(namespace: str, user: dict | None, loader) -> Any:
    cached = _control_room_cache_get(namespace, user)
    if cached is not None:
        return cached
    ttl = _control_room_cache_ttl()
    if ttl <= 0:
        return await loader()
    key = _control_room_cache_key(namespace, user)
    lock = _CONTROL_ROOM_READ_CACHE_LOCKS.setdefault(key, asyncio.Lock())
    async with lock:
        cached = _control_room_cache_get(namespace, user)
        if cached is not None:
            return cached
        return _control_room_cache_set(namespace, user, await loader())


def _control_room_cache_invalidate(user: dict | None) -> None:
    identity = _control_room_cache_identity(user)
    keys = [key for key in _CONTROL_ROOM_READ_CACHE if len(key) == 2 and key[1] == identity]
    for key in keys:
        _CONTROL_ROOM_READ_CACHE.pop(key, None)


async def _invalidate_after_write(user: dict, operation: Any) -> Any:
    result = await operation
    _control_room_cache_invalidate(user)
    return result


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.get("/summary", dependencies=[Depends(require_permission("datasets.read"))])
async def control_room_summary(user: dict = Depends(require_authenticated)):
    return await _control_room_cache_get_or_set("summary", user, lambda: control_room_service.summary(user))


@router.get("/dashboard", dependencies=[Depends(require_permission("datasets.read"))])
async def control_room_dashboard(user: dict = Depends(require_authenticated)):
    return await _control_room_cache_get_or_set("dashboard", user, lambda: control_room_service.dashboard(user))


@router.get("/sap-successfactors/gold-kpis", dependencies=[Depends(require_permission("datasets.read"))])
async def control_room_sap_successfactors_gold_kpis(user: dict = Depends(require_authenticated)):
    return await _control_room_cache_get_or_set(
        "sap-successfactors-gold-kpis",
        user,
        lambda: control_room_service.sap_successfactors_gold_kpis(user),
    )


@router.get("/sap-successfactors/talent-kpis", dependencies=[Depends(require_permission("datasets.read"))])
async def control_room_sap_successfactors_talent_kpis(user: dict = Depends(require_authenticated)):
    return await _control_room_cache_get_or_set(
        "sap-successfactors-talent-kpis",
        user,
        lambda: control_room_service.sap_successfactors_talent_kpis(user),
    )


@router.get("/sap-successfactors/talent/overview", dependencies=[Depends(require_permission("datasets.read"))])
async def control_room_sap_successfactors_talent_overview(user: dict = Depends(require_authenticated)):
    return await _control_room_cache_get_or_set(
        "sap-successfactors-talent-overview",
        user,
        lambda: control_room_service.sap_successfactors_talent_overview(user),
    )


@router.get("/sap-successfactors/talent/9box", dependencies=[Depends(require_permission("datasets.read"))])
async def control_room_sap_successfactors_talent_9box(user: dict = Depends(require_authenticated)):
    return await _control_room_cache_get_or_set(
        "sap-successfactors-talent-9box",
        user,
        lambda: control_room_service.sap_successfactors_talent_9box(user),
    )


@router.get("/sap-successfactors/talent/9box/{box_id}", dependencies=[Depends(require_permission("datasets.read"))])
async def control_room_sap_successfactors_talent_9box_box(
    box_id: str,
    user: dict = Depends(require_authenticated),
):
    return await _control_room_cache_get_or_set(
        f"sap-successfactors-talent-9box-{box_id}",
        user,
        lambda: control_room_service.sap_successfactors_talent_9box_box(user, box_id),
    )


@router.get("/sap-successfactors/talent/anomalies", dependencies=[Depends(require_permission("datasets.read"))])
async def control_room_sap_successfactors_talent_anomalies(user: dict = Depends(require_authenticated)):
    return await _control_room_cache_get_or_set(
        "sap-successfactors-talent-anomalies",
        user,
        lambda: control_room_service.sap_successfactors_talent_anomalies(user),
    )


@router.get("/sap-successfactors/talent/metadata-readiness", dependencies=[Depends(require_permission("datasets.read"))])
async def control_room_sap_successfactors_talent_metadata_readiness(user: dict = Depends(require_authenticated)):
    return await _control_room_cache_get_or_set(
        "sap-successfactors-talent-metadata-readiness",
        user,
        lambda: control_room_service.sap_successfactors_talent_metadata_readiness(user),
    )


@router.post(
    "/sap-successfactors/talent/actions/preview",
    dependencies=[Depends(require_csrf), Depends(require_permission("datasets.read"))],
)
async def control_room_sap_successfactors_talent_action_preview(
    body: dict = Body(default_factory=dict),
    user: dict = Depends(require_authenticated),
):
    return await control_room_service.sap_successfactors_talent_action_preview(
        user,
        body if isinstance(body, dict) else {},
    )


@router.get("/ops/summary", dependencies=[Depends(require_permission("datasets.read"))])
async def control_room_ops_summary(user: dict = Depends(require_authenticated)):
    """Lightweight, pollable operational summary (persisted state only)."""
    return await control_room_service.ops_summary(user)


@router.get("/decision-intelligence/runs", dependencies=[Depends(require_permission("datasets.read"))])
async def control_room_decision_intelligence_runs(
    limit: int = Query(default=50, ge=1, le=250),
    user: dict = Depends(require_authenticated),
):
    return await intelligence_history.list_runs(user, limit=limit)


@router.get("/decision-intelligence/runs/{run_id}", dependencies=[Depends(require_permission("datasets.read"))])
async def control_room_decision_intelligence_run_detail(
    run_id: str,
    user: dict = Depends(require_authenticated),
):
    return await intelligence_history.get_run(user, run_id)


@router.get("/decision-intelligence/history", dependencies=[Depends(require_permission("datasets.read"))])
async def control_room_decision_intelligence_history(
    limit: int = Query(default=100, ge=1, le=500),
    user: dict = Depends(require_authenticated),
):
    return await intelligence_history.list_history(user, limit=limit)


@router.get("/decision-intelligence/calibration", dependencies=[Depends(require_permission("datasets.read"))])
async def control_room_decision_intelligence_calibration(
    min_outcomes_required: int = Query(default=10, ge=1, le=1000),
    user: dict = Depends(require_authenticated),
):
    return await intelligence_history.calibration_report(
        user,
        min_outcomes_required=min_outcomes_required,
    )


@router.get("/alerts", dependencies=[Depends(require_permission("datasets.read"))])
async def control_room_alerts(user: dict = Depends(require_authenticated)):
    return await control_room_service.list_alerts(user)


@router.post(
    "/alerts/{item_id}/ack",
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
)
async def control_room_acknowledge_alert(
    item_id: str,
    request: Request,
    body: dict = Body(default_factory=dict),
    user: dict = Depends(require_authenticated),
):
    return await _invalidate_after_write(
        user,
        control_room_service.acknowledge_alert(
            item_id,
            user,
            body=body if isinstance(body, dict) else {},
            ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        ),
    )


@router.post(
    "/alerts/{item_id}/snooze",
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
)
async def control_room_snooze_alert(
    item_id: str,
    request: Request,
    body: dict = Body(default_factory=dict),
    user: dict = Depends(require_authenticated),
):
    return await _invalidate_after_write(
        user,
        control_room_service.snooze_alert(
            item_id,
            user,
            body=body if isinstance(body, dict) else {},
            ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        ),
    )


@router.post(
    "/alerts/{item_id}/assign",
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
)
async def control_room_assign_alert(
    item_id: str,
    request: Request,
    body: dict = Body(default_factory=dict),
    user: dict = Depends(require_authenticated),
):
    return await _invalidate_after_write(
        user,
        control_room_service.assign_alert(
            item_id,
            user,
            body=body if isinstance(body, dict) else {},
            ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        ),
    )


@router.post(
    "/alerts/{item_id}/false-positive",
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
)
async def control_room_false_positive_alert(
    item_id: str,
    request: Request,
    body: dict = Body(default_factory=dict),
    user: dict = Depends(require_authenticated),
):
    return await _invalidate_after_write(
        user,
        control_room_service.mark_alert_false_positive(
            item_id,
            user,
            body=body if isinstance(body, dict) else {},
            ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        ),
    )


@router.get("/anomalies", dependencies=[Depends(require_permission("datasets.read"))])
async def control_room_anomalies(user: dict = Depends(require_authenticated)):
    return await control_room_service.list_anomalies(user)


@router.get("/items/{item_id}", dependencies=[Depends(require_permission("datasets.read"))])
async def control_room_item_detail(item_id: str, user: dict = Depends(require_authenticated)):
    return await control_room_service.get_item(item_id, user)


@router.get("/items/{item_id}/impact", dependencies=[Depends(require_permission("datasets.read"))])
async def control_room_item_impact(item_id: str, user: dict = Depends(require_authenticated)):
    return await control_room_service.get_item_impact(item_id, user)


@router.get("/items/{item_id}/activity", dependencies=[Depends(require_permission("datasets.read"))])
async def control_room_item_activity(item_id: str, user: dict = Depends(require_authenticated)):
    return await control_room_service.get_item_activity(item_id, user)


@router.get("/items/{item_id}/action-runs", dependencies=[Depends(require_permission("datasets.read"))])
async def control_room_item_action_runs(item_id: str, user: dict = Depends(require_authenticated)):
    return await control_room_service.list_item_action_runs(item_id, user)


@router.get("/items/{item_id}/outcomes", dependencies=[Depends(require_permission("datasets.read"))])
async def control_room_item_outcomes(item_id: str, user: dict = Depends(require_authenticated)):
    return await control_room_service.list_item_outcomes(item_id, user)


@router.post(
    "/items/{item_id}/step",
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
)
async def control_room_record_item_step(
    item_id: str,
    request: Request,
    body: dict = Body(default_factory=dict),
    user: dict = Depends(require_authenticated),
):
    step_id = body.get("step_id") if isinstance(body, dict) else None
    note = body.get("note") if isinstance(body, dict) else None
    control_id = body.get("control_id") if isinstance(body, dict) else None
    return await _invalidate_after_write(
        user,
        control_room_service.record_item_step(
            item_id,
            str(step_id or ""),
            user,
            note=str(note or ""),
            control_id=str(control_id or ""),
            ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        ),
    )


@router.post(
    "/items/{item_id}/lessons",
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
)
async def control_room_create_item_lesson(
    item_id: str,
    request: Request,
    body: dict = Body(default_factory=dict),
    user: dict = Depends(require_authenticated),
):
    return await _invalidate_after_write(
        user,
        control_room_service.create_item_lesson(
            item_id,
            body if isinstance(body, dict) else {},
            user,
            ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        ),
    )


@router.post(
    "/items/{item_id}/outcomes",
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
)
async def control_room_record_item_outcome(
    item_id: str,
    request: Request,
    body: dict = Body(default_factory=dict),
    user: dict = Depends(require_authenticated),
):
    return await _invalidate_after_write(
        user,
        control_room_service.record_item_outcome(
            item_id,
            body if isinstance(body, dict) else {},
            user,
            ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        ),
    )


@router.post(
    "/items/{item_id}/lessons/{lesson_id}/apply",
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
)
async def control_room_apply_item_lesson(
    item_id: str,
    lesson_id: int,
    request: Request,
    body: dict = Body(default_factory=dict),
    user: dict = Depends(require_authenticated),
):
    return await _invalidate_after_write(
        user,
        control_room_service.apply_item_lesson(
            item_id,
            lesson_id,
            body if isinstance(body, dict) else {},
            user,
            ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        ),
    )


@router.post(
    "/items/{item_id}/control/{control_id}",
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
)
async def control_room_update_item_control(
    item_id: str,
    control_id: str,
    request: Request,
    body: dict = Body(default_factory=dict),
    user: dict = Depends(require_authenticated),
):
    return await _invalidate_after_write(
        user,
        control_room_service.update_item_control(
            item_id,
            control_id,
            body if isinstance(body, dict) else {},
            user,
            ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        ),
    )


@router.get("/anomalies/{anomaly_id}", dependencies=[Depends(require_permission("datasets.read"))])
async def control_room_anomaly_detail(anomaly_id: str, user: dict = Depends(require_authenticated)):
    return await control_room_service.get_anomaly(anomaly_id, user)


@router.post(
    "/anomalies/{anomaly_id}/decision",
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
)
async def control_room_create_decision(
    anomaly_id: str,
    request: Request,
    user: dict = Depends(require_authenticated),
):
    return await _invalidate_after_write(
        user,
        control_room_service.create_decision_for_anomaly(
            anomaly_id,
            user,
            ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        ),
    )


@router.post(
    "/items/{item_id}/decision",
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
)
async def control_room_create_item_decision(
    item_id: str,
    request: Request,
    user: dict = Depends(require_authenticated),
):
    return await _invalidate_after_write(
        user,
        control_room_service.create_decision_for_item(
            item_id,
            user,
            ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        ),
    )


@router.post(
    "/items/{item_id}/option",
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
)
async def control_room_select_item_option(
    item_id: str,
    request: Request,
    body: dict = Body(default_factory=dict),
    user: dict = Depends(require_authenticated),
):
    option_id = body.get("option_id") if isinstance(body, dict) else None
    return await _invalidate_after_write(
        user,
        control_room_service.select_item_option(
            item_id,
            str(option_id or ""),
            user,
            ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        ),
    )


@router.post(
    "/items/{item_id}/action-preview",
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
)
async def control_room_action_preview(
    item_id: str,
    request: Request,
    body: dict = Body(default_factory=dict),
    user: dict = Depends(require_authenticated),
):
    template_id = body.get("template_id") if isinstance(body, dict) else None
    return await _invalidate_after_write(
        user,
        control_room_service.action_preview(
            item_id,
            user,
            template_id=str(template_id) if template_id else None,
            ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        ),
    )


@router.post(
    "/items/{item_id}/action-dry-run",
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
)
async def control_room_action_dry_run(
    item_id: str,
    request: Request,
    body: dict = Body(default_factory=dict),
    user: dict = Depends(require_authenticated),
):
    template_id = body.get("template_id") if isinstance(body, dict) else None
    return await _invalidate_after_write(
        user,
        control_room_service.action_dry_run(
            item_id,
            user,
            template_id=str(template_id) if template_id else None,
            ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        ),
    )


@router.post(
    "/items/{item_id}/auto-run",
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
)
async def control_room_auto_run_item(
    item_id: str,
    request: Request,
    user: dict = Depends(require_authenticated),
):
    return await _invalidate_after_write(
        user,
        control_room_service.run_auto_item(
            item_id,
            user,
            ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        ),
    )


@router.post(
    "/items/{item_id}/execute",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("control_room.write")),
        Depends(require_permission("control_room.execute")),
    ],
)
async def control_room_execute_item(
    item_id: str,
    request: Request,
    body: dict = Body(default_factory=dict),
    user: dict = Depends(require_authenticated),
):
    template_id = None
    confirm_execute = False
    idempotency_key = None
    if isinstance(body, dict):
        template_id = body.get("template_id")
        confirm_execute = body.get("confirm_execute") or body.get("confirmation")
        idempotency_key = body.get("idempotency_key")
    return await _invalidate_after_write(
        user,
        control_room_service.execute_item(
            item_id,
            user,
            template_id=str(template_id) if template_id else None,
            confirm_execute=confirm_execute,
            idempotency_key=str(idempotency_key).strip()[:128] if idempotency_key else None,
            ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        ),
    )


@router.post(
    "/anomalies/{anomaly_id}/approve",
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
)
async def control_room_approve(
    anomaly_id: str,
    request: Request,
    body: dict = Body(default_factory=dict),
    user: dict = Depends(require_authenticated),
):
    decision_id = None
    if isinstance(body, dict) and body.get("decision_id") is not None:
        decision_id = int(body["decision_id"])
    return await _invalidate_after_write(
        user,
        control_room_service.approve_anomaly(
            anomaly_id,
            user,
            decision_id=decision_id,
            ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        ),
    )


@router.post(
    "/items/{item_id}/approve",
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
)
async def control_room_approve_item(
    item_id: str,
    request: Request,
    body: dict = Body(default_factory=dict),
    user: dict = Depends(require_authenticated),
):
    decision_id = None
    if isinstance(body, dict) and body.get("decision_id") is not None:
        decision_id = int(body["decision_id"])
    return await _invalidate_after_write(
        user,
        control_room_service.approve_item(
            item_id,
            user,
            decision_id=decision_id,
            ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        ),
    )


@router.post(
    "/items/{item_id}/dismiss",
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
)
async def control_room_dismiss_item(
    item_id: str,
    request: Request,
    body: dict = Body(default_factory=dict),
    user: dict = Depends(require_authenticated),
):
    reason = body.get("reason") if isinstance(body, dict) else None
    return await _invalidate_after_write(
        user,
        control_room_service.dismiss_item(
            item_id,
            user,
            reason=str(reason or ""),
            ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        ),
    )


@router.post(
    "/items/{item_id}/reopen",
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
)
async def control_room_reopen_item(
    item_id: str,
    request: Request,
    body: dict = Body(default_factory=dict),
    user: dict = Depends(require_authenticated),
):
    reason = body.get("reason") if isinstance(body, dict) else None
    return await _invalidate_after_write(
        user,
        control_room_service.reopen_item(
            item_id,
            user,
            reason=str(reason or ""),
            ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        ),
    )


@router.get("/thresholds", dependencies=[Depends(require_permission("datasets.read"))])
async def control_room_thresholds(user: dict = Depends(require_authenticated)):
    return await control_room_service.list_thresholds(user)


@router.post(
    "/thresholds",
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
)
async def control_room_upsert_threshold(
    request: Request,
    body: dict = Body(default_factory=dict),
    user: dict = Depends(require_authenticated),
):
    return await _invalidate_after_write(
        user,
        control_room_service.upsert_threshold(
            body if isinstance(body, dict) else {},
            user,
            ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        ),
    )


@router.patch(
    "/thresholds",
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
)
async def control_room_patch_threshold(
    request: Request,
    body: dict = Body(default_factory=dict),
    user: dict = Depends(require_authenticated),
):
    return await _invalidate_after_write(
        user,
        control_room_service.upsert_threshold(
            body if isinstance(body, dict) else {},
            user,
            ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        ),
    )


@router.get("/lessons", dependencies=[Depends(require_permission("datasets.read"))])
async def control_room_lessons(
    cartridge_id: str | None = Query(default=None),
    anomaly_type: str | None = Query(default=None),
    item_id: str | None = Query(default=None),
    user: dict = Depends(require_authenticated),
):
    return await control_room_service.list_lessons(
        user,
        cartridge_id=cartridge_id,
        anomaly_type=anomaly_type,
        item_id=item_id,
    )
