from __future__ import annotations

# fmt: off

import logging
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response

from app.dependencies import require_authenticated
from app.schemas.control_room_action_requests import (
    ControlRoomActionHandleRequest,
)
from app.schemas.control_room_agent_memory_responses import (
    ControlRoomAgentMemoryResponse,
)
from app.schemas.control_room_domain_kpi_responses import (
    ControlRoomFinanceKpisResponse,
    ControlRoomOperationsKpisResponse,
    ControlRoomRiskKpisResponse,
    ControlRoomSapB1ExpiryKpisResponse,
    ControlRoomSapB1MarginKpisResponse,
    ControlRoomSapB1SalesKpisResponse,
    ControlRoomSapB1SemaforoKpisResponse,
    ControlRoomSapB1SupplyKpisResponse,
)
from app.schemas.control_room_experience_actions import ExperienceActionPreviewResponse
from app.schemas.control_room_alert_mutation_responses import (
    ControlRoomAlertMutationResponse,
    project_alert_mutation_response,
)
from app.schemas.control_room_legacy_responses import (
    ControlRoomAgentsOpsResponse,
    ControlRoomBusinessSummaryResponse,
    ControlRoomDecisionIntelligenceCalibrationResponse,
    ControlRoomDecisionIntelligenceHistoryResponse,
    ControlRoomDecisionIntelligenceRunDetailResponse,
    ControlRoomDecisionIntelligenceRunsResponse,
    ControlRoomGoldKpisResponse,
    ControlRoomLessonsResponse,
    ControlRoomLegacyActionRunsResponse,
    ControlRoomLegacyActivityResponse,
    ControlRoomLegacyAlertsResponse,
    ControlRoomLegacyAnomaliesResponse,
    ControlRoomLegacyDashboardResponse,
    ControlRoomLegacyImpactResponse,
    ControlRoomLegacyItemResponse,
    ControlRoomLegacyOutcomesResponse,
    ControlRoomMarketValidationResponse,
    ControlRoomOpsSummaryResponse,
    ControlRoomReadinessResponse,
    ControlRoomTalentAnomaliesResponse,
    ControlRoomTalentKpisResponse,
    ControlRoomTalentMetadataReadinessResponse,
    ControlRoomTalentNineBoxResponse,
    ControlRoomTalentOverviewResponse,
    ControlRoomTalentRosterResponse,
    ControlRoomTalentWorkforceTrendsResponse,
    ControlRoomThresholdsResponse,
    project_public_control_room_response,
)
from app.schemas.control_room_state_mutation_requests import (
    ControlRoomApprovalRequest,
    ControlRoomThresholdRequest,
)
from app.schemas.control_room_state_mutation_responses import (
    ControlRoomApprovalMutationResponse,
    ControlRoomDecisionMutationResponse,
    ControlRoomDismissMutationResponse,
    ControlRoomOptionMutationResponse,
    ControlRoomReopenMutationResponse,
    ControlRoomThresholdMutationResponse,
    project_approval_mutation_response,
    project_decision_mutation_response,
    project_dismiss_mutation_response,
    project_option_mutation_response,
    project_reopen_mutation_response,
    project_threshold_mutation_response,
)
from app.schemas.control_room_workflow_mutation_responses import (
    ControlRoomApplyLessonMutationResponse,
    ControlRoomControlMutationResponse,
    ControlRoomCreateLessonMutationResponse,
    ControlRoomOutcomeMutationResponse,
    ControlRoomStepMutationResponse,
    project_apply_lesson_mutation_response,
    project_control_mutation_response,
    project_create_lesson_mutation_response,
    project_outcome_mutation_response,
    project_step_mutation_response,
)
from app.services.auth import verify_internal_api_key
from app.services import control_room_service
from app.services.control_room.authorization_cache import (
    READ_CACHE as _CONTROL_ROOM_READ_CACHE,
    READ_CACHE_LOCKS as _CONTROL_ROOM_READ_CACHE_LOCKS,
    cache_get_or_set as _control_room_cache_get_or_set,
    cache_invalidate as _control_room_cache_invalidate,
)
from app.services.control_room.cache_identity import (
    authorization_cache_identity as _control_room_cache_identity,
)
from app.services.control_room.business_cartridge_scope import (
    business_cartridge_allowed,
)
from app.services.control_room.business_action_handle import (
    resolve_business_action_handle,
)
from app.services.csrf import require_csrf
from app.services.intelligence import history as intelligence_history
from app.services.intelligence import market_decision_validation
from app.services.control_room import cycle_blackboard
from app.services.permissions import require_permission
from app.services.security_context import build_security_context, verify_signed_security_context
from app.routers.control_room_surfaces import router as surfaces_router


router = APIRouter(prefix="/api/control-room", tags=["Control Room"])
router.include_router(surfaces_router)


_INTERNAL_OPERATIONAL_VIEWS = frozenset(
    {
        "agents_ops",
        "alerts",
        "dashboard",
        "decision_intelligence_calibration",
        "decision_intelligence_history",
        "decision_intelligence_runs",
        "ops_summary",
        "sap_successfactors_market_validation",
        "sap_successfactors_talent_metadata_readiness",
    }
)


def _require_readiness_cartridge(user: dict, cartridge_id: str) -> None:
    if not business_cartridge_allowed(user, cartridge_id):
        raise HTTPException(403, "cartridge not allowed for active workspace")


async def _invalidate_after_write(user: dict, operation: Any) -> Any:
    result = await operation
    _control_room_cache_invalidate(user)
    return result


logger = logging.getLogger(__name__)


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _control_room_internal_user(
    security_context: dict[str, Any],
    internal_service: str,
) -> dict[str, Any]:
    if internal_service != "mcp-infra":
        raise HTTPException(
            status_code=403,
            detail="only mcp-infra can use this internal route",
        )
    try:
        ctx = verify_signed_security_context(security_context)
    except Exception as exc:
        raise HTTPException(
            status_code=403,
            detail=f"invalid security_context: {exc}",
        ) from exc
    if not ctx.get("trusted"):
        raise HTTPException(status_code=403, detail="trusted security_context required")
    permissions = {str(item) for item in (ctx.get("permissions") or [])}
    if not permissions & {"datasets.read", "operations.read"}:
        raise HTTPException(status_code=403, detail="read permission required")
    tenant_id = str(ctx.get("tenant_id") or "").strip()
    workspace_id = str(ctx.get("workspace_id") or "").strip()
    if not tenant_id or not workspace_id:
        raise HTTPException(status_code=403, detail="tenant/workspace scope required")
    return {
        "id": ctx.get("user_id") or 0,
        "email": ctx.get("email") or "mcp-infra@omega.local",
        "role": ctx.get("role") or "agent",
        "workspace_role": ctx.get("workspace_role"),
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "active_tenant_id": tenant_id,
        "active_workspace_id": workspace_id,
        "allowed_cartridges": list(ctx.get("allowed_cartridges") or []),
        "_effective_permissions": sorted(permissions),
        "agent_id": ctx.get("agent_id"),
        "agent_slug": ctx.get("agent_slug"),
        "agent_run_id": ctx.get("agent_run_id"),
        "security_context_source": ctx.get("source"),
    }


def _bounded_int(value: Any, default: int, *, lower: int, upper: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(lower, min(number, upper))


def _require_internal_view_permission(view: str, user: dict[str, Any]) -> None:
    permissions = {str(item) for item in user.get("_effective_permissions") or []}
    required = "operations.read" if view in _INTERNAL_OPERATIONAL_VIEWS else "datasets.read"
    if required not in permissions:
        raise HTTPException(status_code=403, detail=f"permission required: {required}")


async def _control_room_internal_view(
    view: str,
    user: dict[str, Any],
    params: dict[str, Any],
) -> Any:
    view = str(view or "").strip()
    _require_internal_view_permission(view, user)
    if view == "summary":
        return project_public_control_room_response(
            ControlRoomBusinessSummaryResponse,
            await _control_room_cache_get_or_set(
                "summary", user, lambda: control_room_service.summary(user)
            ),
        )
    if view == "dashboard":
        return project_public_control_room_response(
            ControlRoomLegacyDashboardResponse,
            await _control_room_cache_get_or_set(
                "dashboard", user, lambda: control_room_service.dashboard(user)
            ),
        )
    if view == "ops_summary":
        return project_public_control_room_response(
            ControlRoomOpsSummaryResponse,
            await control_room_service.ops_summary(user),
        )
    if view == "agents_ops":
        limit = _bounded_int(params.get("limit"), 12, lower=1, upper=50)
        return project_public_control_room_response(
            ControlRoomAgentsOpsResponse,
            await _control_room_cache_get_or_set(
                f"agents-ops-{limit}",
                user,
                lambda: control_room_service.agents_ops(user, limit=limit),
            ),
        )
    if view == "alerts":
        return project_public_control_room_response(
            ControlRoomLegacyAlertsResponse,
            await control_room_service.list_alerts(user),
        )
    if view == "sap_successfactors_gold_kpis":
        return project_public_control_room_response(
            ControlRoomGoldKpisResponse,
            await _control_room_cache_get_or_set(
                "sap-successfactors-gold-kpis",
                user,
                lambda: control_room_service.sap_successfactors_gold_kpis(user),
            ),
        )
    if view == "sap_successfactors_talent_kpis":
        return project_public_control_room_response(
            ControlRoomTalentKpisResponse,
            await _control_room_cache_get_or_set(
                "sap-successfactors-talent-kpis",
                user,
                lambda: control_room_service.sap_successfactors_talent_kpis(user),
            ),
        )
    if view == "finance_kpis":
        top_n = _bounded_int(params.get("top_n"), 0, lower=0, upper=10)
        return project_public_control_room_response(
            ControlRoomFinanceKpisResponse,
            await _control_room_cache_get_or_set(
                f"finance-kpis-{top_n}",
                user,
                lambda: control_room_service.finance_kpis(user, top_n=top_n),
            ),
        )
    if view == "operations_kpis":
        return project_public_control_room_response(
            ControlRoomOperationsKpisResponse,
            await _control_room_cache_get_or_set(
                "operations-kpis",
                user,
                lambda: control_room_service.operations_kpis(user),
            ),
        )
    if view == "risk_kpis":
        top_n = _bounded_int(params.get("top_n"), 0, lower=0, upper=10)
        return project_public_control_room_response(
            ControlRoomRiskKpisResponse,
            await _control_room_cache_get_or_set(
                f"risk-kpis-{top_n}",
                user,
                lambda: control_room_service.risk_kpis(user, top_n=top_n),
            ),
        )
    if view == "sap_b1_margin_kpis":
        top_n = _bounded_int(params.get("top_n"), 0, lower=0, upper=10)
        return project_public_control_room_response(
            ControlRoomSapB1MarginKpisResponse,
            await _control_room_cache_get_or_set(
                f"sap-b1-margin-kpis-{top_n}",
                user,
                lambda: control_room_service.sap_b1_margin_kpis(user, top_n=top_n),
            ),
        )
    if view == "sap_b1_sales_kpis":
        return project_public_control_room_response(
            ControlRoomSapB1SalesKpisResponse,
            await _control_room_cache_get_or_set(
                "sap-b1-sales-kpis", user, lambda: control_room_service.sap_b1_sales_kpis(user)
            ),
        )
    if view == "sap_b1_expiry_kpis":
        return project_public_control_room_response(
            ControlRoomSapB1ExpiryKpisResponse,
            await _control_room_cache_get_or_set(
                "sap-b1-expiry-kpis", user, lambda: control_room_service.sap_b1_expiry_kpis(user)
            ),
        )
    if view == "sap_b1_supply_kpis":
        return project_public_control_room_response(
            ControlRoomSapB1SupplyKpisResponse,
            await _control_room_cache_get_or_set(
                "sap-b1-supply-kpis", user, lambda: control_room_service.sap_b1_supply_kpis(user)
            ),
        )
    if view == "sap_b1_semaforo_kpis":
        return project_public_control_room_response(
            ControlRoomSapB1SemaforoKpisResponse,
            await _control_room_cache_get_or_set(
                "sap-b1-semaforo-kpis", user, lambda: control_room_service.sap_b1_semaforo_kpis(user)
            ),
        )
    if view == "agent_memory":
        subject = params.get("subject")
        subject_text = str(subject).strip() if subject not in (None, "") else None
        if subject_text is not None and len(subject_text) > 200:
            raise HTTPException(status_code=400, detail="subject is too long")
        return project_public_control_room_response(
            ControlRoomAgentMemoryResponse,
            await control_room_service.agent_memory_read(
                user,
                subject=subject_text,
                limit=_bounded_int(params.get("limit"), 10, lower=1, upper=20),
            ),
        )
    if view == "sap_successfactors_workforce_trends":
        return project_public_control_room_response(
            ControlRoomTalentWorkforceTrendsResponse,
            await _control_room_cache_get_or_set(
                "sap-successfactors-workforce-trends",
                user,
                lambda: control_room_service.build_workforce_trends(user),
            ),
        )
    if view == "sap_successfactors_talent_overview":
        return project_public_control_room_response(
            ControlRoomTalentOverviewResponse,
            await _control_room_cache_get_or_set(
                "sap-successfactors-talent-overview",
                user,
                lambda: control_room_service.sap_successfactors_talent_overview(user),
            ),
        )
    if view == "sap_successfactors_talent_9box":
        return project_public_control_room_response(
            ControlRoomTalentNineBoxResponse,
            await _control_room_cache_get_or_set(
                "sap-successfactors-talent-9box",
                user,
                lambda: control_room_service.sap_successfactors_talent_9box(user),
            ),
        )
    if view == "sap_successfactors_talent_metadata_readiness":
        return project_public_control_room_response(
            ControlRoomTalentMetadataReadinessResponse,
            await _control_room_cache_get_or_set(
                "sap-successfactors-talent-metadata-readiness",
                user,
                lambda: control_room_service.sap_successfactors_talent_metadata_readiness(user),
            ),
        )
    if view == "sap_successfactors_market_validation":
        return project_public_control_room_response(
            ControlRoomMarketValidationResponse,
            await _control_room_cache_get_or_set(
                "sap-successfactors-market-validation",
                user,
                lambda: market_decision_validation.get_validation(user),
            ),
        )
    if view == "banxico_readiness":
        from app.services.banxico_readiness import banxico_readiness

        _require_readiness_cartridge(user, "banxico")
        return project_public_control_room_response(
            ControlRoomReadinessResponse,
            await _control_room_cache_get_or_set(
                "banxico-readiness",
                user,
                lambda: banxico_readiness(user),
            ),
        )
    if view == "inegi_readiness":
        from app.services.inegi_readiness import inegi_readiness

        _require_readiness_cartridge(user, "inegi")
        return project_public_control_room_response(
            ControlRoomReadinessResponse,
            await _control_room_cache_get_or_set(
                "inegi-readiness",
                user,
                lambda: inegi_readiness(user),
            ),
        )
    if view == "sec_edgar_readiness":
        from app.services.sec_edgar_readiness import sec_edgar_readiness

        _require_readiness_cartridge(user, "sec_edgar")
        return project_public_control_room_response(
            ControlRoomReadinessResponse,
            await _control_room_cache_get_or_set(
                "sec-edgar-readiness",
                user,
                lambda: sec_edgar_readiness(user),
            ),
        )
    if view == "decision_intelligence_runs":
        limit = _bounded_int(params.get("limit"), 50, lower=1, upper=250)
        return project_public_control_room_response(
            ControlRoomDecisionIntelligenceRunsResponse,
            await intelligence_history.list_runs(user, limit=limit),
        )
    if view == "decision_intelligence_history":
        limit = _bounded_int(params.get("limit"), 100, lower=1, upper=500)
        return project_public_control_room_response(
            ControlRoomDecisionIntelligenceHistoryResponse,
            await intelligence_history.list_history(user, limit=limit),
        )
    if view == "decision_intelligence_calibration":
        min_outcomes_required = _bounded_int(
            params.get("min_outcomes_required"),
            10,
            lower=1,
            upper=1000,
        )
        return project_public_control_room_response(
            ControlRoomDecisionIntelligenceCalibrationResponse,
            await intelligence_history.calibration_report(
                user,
                min_outcomes_required=min_outcomes_required,
            ),
        )
    raise HTTPException(status_code=400, detail=f"unsupported control room view: {view}")


@router.get(
    "/summary",
    response_model=ControlRoomBusinessSummaryResponse,
    dependencies=[Depends(require_permission("datasets.read"))],
)
async def control_room_summary(user: dict = Depends(require_authenticated)):
    return project_public_control_room_response(
        ControlRoomBusinessSummaryResponse,
        await _control_room_cache_get_or_set(
            "summary", user, lambda: control_room_service.summary(user)
        ),
    )


@router.get(
    "/blackboard",
    dependencies=[Depends(require_permission("operations.read"))],
)
async def control_room_blackboard(
    limit: int = Query(default=20, ge=1, le=100),
    user: dict = Depends(require_authenticated),
):
    """E5b — la pizarra del ciclo: que corrio, que encontro, que aprendio y
    como se movieron las creencias (una sola lectura scoped; secciones sin
    filas = listas vacias, jamas relleno)."""
    return await cycle_blackboard.read_blackboard(user, limit=limit)


@router.get(
    "/dashboard",
    response_model=ControlRoomLegacyDashboardResponse,
    dependencies=[Depends(require_permission("operations.read"))],
)
async def control_room_dashboard(user: dict = Depends(require_authenticated)):
    payload = await _control_room_cache_get_or_set(
        "dashboard", user, lambda: control_room_service.dashboard(user)
    )
    return project_public_control_room_response(ControlRoomLegacyDashboardResponse, payload)


@router.get(
    "/sap-successfactors/gold-kpis",
    response_model=ControlRoomGoldKpisResponse,
    dependencies=[Depends(require_permission("datasets.read"))],
)
async def control_room_sap_successfactors_gold_kpis(user: dict = Depends(require_authenticated)):
    return project_public_control_room_response(
        ControlRoomGoldKpisResponse,
        await _control_room_cache_get_or_set(
            "sap-successfactors-gold-kpis",
            user,
            lambda: control_room_service.sap_successfactors_gold_kpis(user),
        ),
    )


@router.get(
    "/sap-successfactors/talent-kpis",
    response_model=ControlRoomTalentKpisResponse,
    dependencies=[Depends(require_permission("datasets.read"))],
)
async def control_room_sap_successfactors_talent_kpis(user: dict = Depends(require_authenticated)):
    payload = await _control_room_cache_get_or_set(
        "sap-successfactors-talent-kpis",
        user,
        lambda: control_room_service.sap_successfactors_talent_kpis(user),
    )
    return project_public_control_room_response(ControlRoomTalentKpisResponse, payload)


@router.get(
    "/sap-successfactors/talent/overview",
    response_model=ControlRoomTalentOverviewResponse,
    dependencies=[Depends(require_permission("datasets.read"))],
)
async def control_room_sap_successfactors_talent_overview(user: dict = Depends(require_authenticated)):
    payload = await _control_room_cache_get_or_set(
        "sap-successfactors-talent-overview",
        user,
        lambda: control_room_service.sap_successfactors_talent_overview(user),
    )
    return project_public_control_room_response(ControlRoomTalentOverviewResponse, payload)


@router.get(
    "/sap-successfactors/talent/9box",
    response_model=ControlRoomTalentNineBoxResponse,
    dependencies=[Depends(require_permission("datasets.read"))],
)
async def control_room_sap_successfactors_talent_9box(user: dict = Depends(require_authenticated)):
    payload = await _control_room_cache_get_or_set(
        "sap-successfactors-talent-9box",
        user,
        lambda: control_room_service.sap_successfactors_talent_9box(user),
    )
    return project_public_control_room_response(ControlRoomTalentNineBoxResponse, payload)


@router.get(
    "/sap-successfactors/talent/9box/{box_id}",
    response_model=ControlRoomTalentRosterResponse,
    dependencies=[Depends(require_permission("datasets.read"))],
)
async def control_room_sap_successfactors_talent_9box_box(
    box_id: str,
    user: dict = Depends(require_authenticated),
):
    payload = await _control_room_cache_get_or_set(
        f"sap-successfactors-talent-9box-{box_id}",
        user,
        lambda: control_room_service.sap_successfactors_talent_9box_box(user, box_id),
    )
    return project_public_control_room_response(ControlRoomTalentRosterResponse, payload)


@router.get(
    "/sap-successfactors/talent/anomalies",
    response_model=ControlRoomTalentAnomaliesResponse,
    dependencies=[Depends(require_permission("datasets.read"))],
)
async def control_room_sap_successfactors_talent_anomalies(user: dict = Depends(require_authenticated)):
    payload = await _control_room_cache_get_or_set(
        "sap-successfactors-talent-anomalies",
        user,
        lambda: control_room_service.sap_successfactors_talent_anomalies(user),
    )
    return project_public_control_room_response(ControlRoomTalentAnomaliesResponse, payload)


@router.get(
    "/sap-successfactors/talent/metadata-readiness",
    response_model=ControlRoomTalentMetadataReadinessResponse,
    dependencies=[Depends(require_permission("operations.read"))],
)
async def control_room_sap_successfactors_talent_metadata_readiness(user: dict = Depends(require_authenticated)):
    payload = await _control_room_cache_get_or_set(
        "sap-successfactors-talent-metadata-readiness",
        user,
        lambda: control_room_service.sap_successfactors_talent_metadata_readiness(user),
    )
    return project_public_control_room_response(
        ControlRoomTalentMetadataReadinessResponse,
        payload,
    )


@router.get(
    "/banxico/readiness",
    response_model=ControlRoomReadinessResponse,
    dependencies=[Depends(require_permission("datasets.read"))],
)
async def control_room_banxico_readiness(user: dict = Depends(require_authenticated)):
    from app.services.banxico_readiness import banxico_readiness

    _require_readiness_cartridge(user, "banxico")
    return project_public_control_room_response(
        ControlRoomReadinessResponse,
        await _control_room_cache_get_or_set(
            "banxico-readiness", user, lambda: banxico_readiness(user)
        ),
    )


@router.get(
    "/inegi/readiness",
    response_model=ControlRoomReadinessResponse,
    dependencies=[Depends(require_permission("datasets.read"))],
)
async def control_room_inegi_readiness(user: dict = Depends(require_authenticated)):
    from app.services.inegi_readiness import inegi_readiness

    _require_readiness_cartridge(user, "inegi")
    return project_public_control_room_response(
        ControlRoomReadinessResponse,
        await _control_room_cache_get_or_set(
            "inegi-readiness", user, lambda: inegi_readiness(user)
        ),
    )


@router.get(
    "/sec-edgar/readiness",
    response_model=ControlRoomReadinessResponse,
    dependencies=[Depends(require_permission("datasets.read"))],
)
async def control_room_sec_edgar_readiness(user: dict = Depends(require_authenticated)):
    from app.services.sec_edgar_readiness import sec_edgar_readiness

    _require_readiness_cartridge(user, "sec_edgar")
    return project_public_control_room_response(
        ControlRoomReadinessResponse,
        await _control_room_cache_get_or_set(
            "sec-edgar-readiness", user, lambda: sec_edgar_readiness(user)
        ),
    )


@router.post(
    "/sap-successfactors/talent/actions/preview",
    status_code=410,
    response_class=Response,
)
async def control_room_sap_successfactors_talent_action_preview(
    user: dict = Depends(require_authenticated),
):
    return Response(status_code=410)


@router.get(
    "/sap-successfactors/market-validation",
    response_model=ControlRoomMarketValidationResponse,
    dependencies=[Depends(require_permission("operations.read"))],
)
async def control_room_sap_successfactors_market_validation(
    user: dict = Depends(require_authenticated),
):
    return project_public_control_room_response(
        ControlRoomMarketValidationResponse,
        await _control_room_cache_get_or_set(
            "sap-successfactors-market-validation",
            user,
            lambda: market_decision_validation.get_validation(user),
        ),
    )


@router.post(
    "/sap-successfactors/market-validation/run",
    response_model=ControlRoomMarketValidationResponse,
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("control_room.write")),
    ],
)
async def control_room_sap_successfactors_market_validation_run(
    user: dict = Depends(require_authenticated),
):
    result = await market_decision_validation.run_validation(user)
    _control_room_cache_invalidate(user)
    return project_public_control_room_response(
        ControlRoomMarketValidationResponse,
        result,
    )


@router.get(
    "/ops/summary",
    response_model=ControlRoomOpsSummaryResponse,
    dependencies=[Depends(require_permission("operations.read"))],
)
async def control_room_ops_summary(user: dict = Depends(require_authenticated)):
    """Pollable operational summary over the canonical business projection."""
    return project_public_control_room_response(
        ControlRoomOpsSummaryResponse,
        await control_room_service.ops_summary(user),
    )


@router.get(
    "/agents/ops",
    response_model=ControlRoomAgentsOpsResponse,
    dependencies=[Depends(require_permission("operations.read"))],
)
async def control_room_agents_ops(
    limit: int = Query(default=12, ge=1, le=50),
    user: dict = Depends(require_authenticated),
):
    return project_public_control_room_response(
        ControlRoomAgentsOpsResponse,
        await _control_room_cache_get_or_set(
            f"agents-ops-{limit}",
            user,
            lambda: control_room_service.agents_ops(user, limit=limit),
        ),
    )


@router.get(
    "/decision-intelligence/runs",
    response_model=ControlRoomDecisionIntelligenceRunsResponse,
    dependencies=[Depends(require_permission("operations.read"))],
)
async def control_room_decision_intelligence_runs(
    limit: int = Query(default=50, ge=1, le=250),
    user: dict = Depends(require_authenticated),
):
    return project_public_control_room_response(
        ControlRoomDecisionIntelligenceRunsResponse,
        await intelligence_history.list_runs(user, limit=limit),
    )


@router.get(
    "/decision-intelligence/runs/{run_id}",
    response_model=ControlRoomDecisionIntelligenceRunDetailResponse,
    dependencies=[Depends(require_permission("operations.read"))],
)
async def control_room_decision_intelligence_run_detail(
    run_id: str,
    user: dict = Depends(require_authenticated),
):
    return project_public_control_room_response(
        ControlRoomDecisionIntelligenceRunDetailResponse,
        await intelligence_history.get_run(user, run_id),
    )


@router.get(
    "/decision-intelligence/history",
    response_model=ControlRoomDecisionIntelligenceHistoryResponse,
    dependencies=[Depends(require_permission("operations.read"))],
)
async def control_room_decision_intelligence_history(
    limit: int = Query(default=100, ge=1, le=500),
    user: dict = Depends(require_authenticated),
):
    return project_public_control_room_response(
        ControlRoomDecisionIntelligenceHistoryResponse,
        await intelligence_history.list_history(user, limit=limit),
    )


@router.get(
    "/decision-intelligence/calibration",
    response_model=ControlRoomDecisionIntelligenceCalibrationResponse,
    dependencies=[Depends(require_permission("operations.read"))],
)
async def control_room_decision_intelligence_calibration(
    min_outcomes_required: int = Query(default=10, ge=1, le=1000),
    user: dict = Depends(require_authenticated),
):
    return project_public_control_room_response(
        ControlRoomDecisionIntelligenceCalibrationResponse,
        await intelligence_history.calibration_report(
            user,
            min_outcomes_required=min_outcomes_required,
        ),
    )


@router.get(
    "/alerts",
    response_model=ControlRoomLegacyAlertsResponse,
    dependencies=[Depends(require_permission("operations.read"))],
)
async def control_room_alerts(user: dict = Depends(require_authenticated)):
    return project_public_control_room_response(
        ControlRoomLegacyAlertsResponse,
        await control_room_service.list_alerts(user),
    )


@router.post("/internal/read")
async def control_room_internal_read(
    body: dict = Body(default_factory=dict),
    internal_service: str = Depends(verify_internal_api_key),
):
    """Scoped Control Room read bridge for MCP tools.

    This route intentionally exposes only read views and rebuilds the user
    scope from the signed security context. It does not accept tenant,
    workspace or user overrides in the payload.
    """
    payload = body if isinstance(body, dict) else {}
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    user = _control_room_internal_user(
        payload.get("security_context") if isinstance(payload.get("security_context"), dict) else {},
        internal_service,
    )
    view = str(payload.get("view") or "").strip()
    logger.info(
        "control_room.internal_read view=%r internal_service=%r"
        " security_context_source=%r tenant_id=%s workspace_id=%s"
        " agent_id=%r agent_run_id=%r",
        view[:64],
        internal_service,
        user.get("security_context_source"),
        user["tenant_id"],
        user["workspace_id"],
        user.get("agent_id"),
        user.get("agent_run_id"),
    )
    data = await _control_room_internal_view(view, user, params)
    return {
        "ok": True,
        "view": view,
        "tenant_id": user["tenant_id"],
        "workspace_id": user["workspace_id"],
        "data": data,
    }


@router.post(
    "/alerts/{item_id}/ack",
    response_model=ControlRoomAlertMutationResponse,
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
)
async def control_room_acknowledge_alert(
    item_id: str,
    request: Request,
    body: dict = Body(default_factory=dict),
    user: dict = Depends(require_authenticated),
):
    return project_alert_mutation_response(
        await _invalidate_after_write(
            user,
            control_room_service.acknowledge_alert(
                item_id,
                user,
                body=body if isinstance(body, dict) else {},
                ip=_client_ip(request),
                user_agent=request.headers.get("user-agent"),
            ),
        ),
    )


@router.post(
    "/alerts/{item_id}/snooze",
    response_model=ControlRoomAlertMutationResponse,
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
)
async def control_room_snooze_alert(
    item_id: str,
    request: Request,
    body: dict = Body(default_factory=dict),
    user: dict = Depends(require_authenticated),
):
    return project_alert_mutation_response(
        await _invalidate_after_write(
            user,
            control_room_service.snooze_alert(
                item_id,
                user,
                body=body if isinstance(body, dict) else {},
                ip=_client_ip(request),
                user_agent=request.headers.get("user-agent"),
            ),
        ),
    )


@router.post(
    "/alerts/{item_id}/assign",
    response_model=ControlRoomAlertMutationResponse,
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
)
async def control_room_assign_alert(
    item_id: str,
    request: Request,
    body: dict = Body(default_factory=dict),
    user: dict = Depends(require_authenticated),
):
    return project_alert_mutation_response(
        await _invalidate_after_write(
            user,
            control_room_service.assign_alert(
                item_id,
                user,
                body=body if isinstance(body, dict) else {},
                ip=_client_ip(request),
                user_agent=request.headers.get("user-agent"),
            ),
        ),
    )


@router.post(
    "/alerts/{item_id}/false-positive",
    response_model=ControlRoomAlertMutationResponse,
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
)
async def control_room_false_positive_alert(
    item_id: str,
    request: Request,
    body: dict = Body(default_factory=dict),
    user: dict = Depends(require_authenticated),
):
    return project_alert_mutation_response(
        await _invalidate_after_write(
            user,
            control_room_service.mark_alert_false_positive(
                item_id,
                user,
                body=body if isinstance(body, dict) else {},
                ip=_client_ip(request),
                user_agent=request.headers.get("user-agent"),
            ),
        ),
    )


@router.get(
    "/anomalies",
    response_model=ControlRoomLegacyAnomaliesResponse,
    dependencies=[Depends(require_permission("operations.read"))],
)
async def control_room_anomalies(user: dict = Depends(require_authenticated)):
    return project_public_control_room_response(
        ControlRoomLegacyAnomaliesResponse,
        await control_room_service.list_anomalies(user),
    )


@router.get(
    "/items/{item_id}",
    response_model=ControlRoomLegacyItemResponse,
    dependencies=[Depends(require_permission("operations.read"))],
)
async def control_room_item_detail(item_id: str, user: dict = Depends(require_authenticated)):
    return project_public_control_room_response(
        ControlRoomLegacyItemResponse,
        await control_room_service.get_item(item_id, user),
    )


@router.get(
    "/items/{item_id}/impact",
    response_model=ControlRoomLegacyImpactResponse,
    dependencies=[Depends(require_permission("operations.read"))],
)
async def control_room_item_impact(item_id: str, user: dict = Depends(require_authenticated)):
    return project_public_control_room_response(
        ControlRoomLegacyImpactResponse,
        await control_room_service.get_item_impact(item_id, user),
    )


@router.get(
    "/items/{item_id}/activity",
    response_model=ControlRoomLegacyActivityResponse,
    dependencies=[Depends(require_permission("operations.read"))],
)
async def control_room_item_activity(item_id: str, user: dict = Depends(require_authenticated)):
    return project_public_control_room_response(
        ControlRoomLegacyActivityResponse,
        await control_room_service.get_item_activity(item_id, user),
    )


@router.get(
    "/items/{item_id}/action-runs",
    response_model=ControlRoomLegacyActionRunsResponse,
    response_model_exclude_none=True,
    dependencies=[Depends(require_permission("operations.read"))],
)
async def control_room_item_action_runs(item_id: str, user: dict = Depends(require_authenticated)):
    return project_public_control_room_response(
        ControlRoomLegacyActionRunsResponse,
        await control_room_service.list_item_action_runs(item_id, user),
    )


@router.get(
    "/items/{item_id}/outcomes",
    response_model=ControlRoomLegacyOutcomesResponse,
    response_model_exclude_none=True,
    dependencies=[Depends(require_permission("operations.read"))],
)
async def control_room_item_outcomes(item_id: str, user: dict = Depends(require_authenticated)):
    return project_public_control_room_response(
        ControlRoomLegacyOutcomesResponse,
        await control_room_service.list_item_outcomes(item_id, user),
    )


@router.post(
    "/items/{item_id}/step",
    response_model=ControlRoomStepMutationResponse,
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
    return project_step_mutation_response(
        await _invalidate_after_write(
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
        ),
    )


@router.post(
    "/items/{item_id}/lessons",
    response_model=ControlRoomCreateLessonMutationResponse,
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
)
async def control_room_create_item_lesson(
    item_id: str,
    request: Request,
    body: dict = Body(default_factory=dict),
    user: dict = Depends(require_authenticated),
):
    return project_create_lesson_mutation_response(
        await _invalidate_after_write(
            user,
            control_room_service.create_item_lesson(
                item_id,
                body if isinstance(body, dict) else {},
                user,
                ip=_client_ip(request),
                user_agent=request.headers.get("user-agent"),
            ),
        ),
    )


@router.post(
    "/items/{item_id}/outcomes",
    response_model=ControlRoomOutcomeMutationResponse,
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
)
async def control_room_record_item_outcome(
    item_id: str,
    request: Request,
    body: dict = Body(default_factory=dict),
    user: dict = Depends(require_authenticated),
):
    return project_outcome_mutation_response(
        await _invalidate_after_write(
            user,
            control_room_service.record_item_outcome(
                item_id,
                body if isinstance(body, dict) else {},
                user,
                ip=_client_ip(request),
                user_agent=request.headers.get("user-agent"),
            ),
        ),
    )


@router.post(
    "/items/{item_id}/lessons/{lesson_id}/apply",
    response_model=ControlRoomApplyLessonMutationResponse,
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
)
async def control_room_apply_item_lesson(
    item_id: str,
    lesson_id: int,
    request: Request,
    body: dict = Body(default_factory=dict),
    user: dict = Depends(require_authenticated),
):
    return project_apply_lesson_mutation_response(
        await _invalidate_after_write(
            user,
            control_room_service.apply_item_lesson(
                item_id,
                lesson_id,
                body if isinstance(body, dict) else {},
                user,
                ip=_client_ip(request),
                user_agent=request.headers.get("user-agent"),
            ),
        ),
    )


@router.post(
    "/items/{item_id}/control/{control_id}",
    response_model=ControlRoomControlMutationResponse,
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
)
async def control_room_update_item_control(
    item_id: str,
    control_id: str,
    request: Request,
    body: dict = Body(default_factory=dict),
    user: dict = Depends(require_authenticated),
):
    return project_control_mutation_response(
        await _invalidate_after_write(
            user,
            control_room_service.update_item_control(
                item_id,
                control_id,
                body if isinstance(body, dict) else {},
                user,
                ip=_client_ip(request),
                user_agent=request.headers.get("user-agent"),
            ),
        ),
    )


@router.get(
    "/anomalies/{anomaly_id}",
    response_model=ControlRoomLegacyItemResponse,
    dependencies=[Depends(require_permission("operations.read"))],
)
async def control_room_anomaly_detail(anomaly_id: str, user: dict = Depends(require_authenticated)):
    return project_public_control_room_response(
        ControlRoomLegacyItemResponse,
        await control_room_service.get_anomaly(anomaly_id, user),
    )


@router.post(
    "/anomalies/{anomaly_id}/decision",
    response_model=ControlRoomDecisionMutationResponse,
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
)
async def control_room_create_decision(
    anomaly_id: str,
    request: Request,
    user: dict = Depends(require_authenticated),
):
    return project_decision_mutation_response(
        await _invalidate_after_write(
            user,
            control_room_service.create_decision_for_anomaly(
                anomaly_id,
                user,
                ip=_client_ip(request),
                user_agent=request.headers.get("user-agent"),
            ),
        ),
    )


@router.post(
    "/items/{item_id}/decision",
    response_model=ControlRoomDecisionMutationResponse,
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
)
async def control_room_create_item_decision(
    item_id: str,
    request: Request,
    user: dict = Depends(require_authenticated),
):
    return project_decision_mutation_response(
        await _invalidate_after_write(
            user,
            control_room_service.create_decision_for_item(
                item_id,
                user,
                ip=_client_ip(request),
                user_agent=request.headers.get("user-agent"),
            ),
        ),
    )


@router.post(
    "/items/{item_id}/option",
    response_model=ControlRoomOptionMutationResponse,
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
)
async def control_room_select_item_option(
    item_id: str,
    request: Request,
    body: dict = Body(default_factory=dict),
    user: dict = Depends(require_authenticated),
):
    option_id = body.get("option_id") if isinstance(body, dict) else None
    return project_option_mutation_response(
        await _invalidate_after_write(
            user,
            control_room_service.select_item_option(
                item_id,
                str(option_id or ""),
                user,
                ip=_client_ip(request),
                user_agent=request.headers.get("user-agent"),
            ),
        ),
    )


@router.post(
    "/actions/preview",
    response_model=ExperienceActionPreviewResponse,
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
)
async def control_room_action_handle_preview(
    request: Request,
    body: ControlRoomActionHandleRequest,
    user: dict = Depends(require_authenticated),
):
    resolved = await resolve_business_action_handle(user, body.action_handle)
    await _invalidate_after_write(
        user,
        control_room_service.action_preview(
            resolved.item_id,
            user,
            template_id=resolved.template_id,
            binding_id=resolved.binding_id,
            ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        ),
    )
    return ExperienceActionPreviewResponse(action_handle=body.action_handle)


@router.post(
    "/items/{item_id}/action-preview",
    status_code=410,
    response_class=Response,
)
async def control_room_action_preview(
    item_id: str,
    user: dict = Depends(require_authenticated),
):
    return Response(status_code=410)


@router.post(
    "/items/{item_id}/action-dry-run",
    status_code=410,
    response_class=Response,
)
async def control_room_action_dry_run(
    item_id: str,
    user: dict = Depends(require_authenticated),
):
    return Response(status_code=410)


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
    status_code=410,
    response_class=Response,
)
async def control_room_execute_item(
    item_id: str,
    user: dict = Depends(require_authenticated),
):
    return Response(status_code=410)


@router.post(
    "/anomalies/{anomaly_id}/approve",
    response_model=ControlRoomApprovalMutationResponse,
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
)
async def control_room_approve(
    anomaly_id: str,
    request: Request,
    body: ControlRoomApprovalRequest,
    user: dict = Depends(require_authenticated),
):
    return project_approval_mutation_response(
        await _invalidate_after_write(
            user,
            control_room_service.approve_anomaly(
                anomaly_id,
                user,
                decision_id=body.decision_id,
                ip=_client_ip(request),
                user_agent=request.headers.get("user-agent"),
            ),
        ),
    )


@router.post(
    "/items/{item_id}/approve",
    response_model=ControlRoomApprovalMutationResponse,
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
)
async def control_room_approve_item(
    item_id: str,
    request: Request,
    body: ControlRoomApprovalRequest,
    user: dict = Depends(require_authenticated),
):
    return project_approval_mutation_response(
        await _invalidate_after_write(
            user,
            control_room_service.approve_item(
                item_id,
                user,
                decision_id=body.decision_id,
                ip=_client_ip(request),
                user_agent=request.headers.get("user-agent"),
            ),
        ),
    )


@router.post(
    "/items/{item_id}/dismiss",
    response_model=ControlRoomDismissMutationResponse,
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
)
async def control_room_dismiss_item(
    item_id: str,
    request: Request,
    body: dict = Body(default_factory=dict),
    user: dict = Depends(require_authenticated),
):
    reason = body.get("reason") if isinstance(body, dict) else None
    return project_dismiss_mutation_response(
        await _invalidate_after_write(
            user,
            control_room_service.dismiss_item(
                item_id,
                user,
                reason=str(reason or ""),
                ip=_client_ip(request),
                user_agent=request.headers.get("user-agent"),
            ),
        ),
    )


@router.post(
    "/items/{item_id}/reopen",
    response_model=ControlRoomReopenMutationResponse,
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
)
async def control_room_reopen_item(
    item_id: str,
    request: Request,
    body: dict = Body(default_factory=dict),
    user: dict = Depends(require_authenticated),
):
    reason = body.get("reason") if isinstance(body, dict) else None
    return project_reopen_mutation_response(
        await _invalidate_after_write(
            user,
            control_room_service.reopen_item(
                item_id,
                user,
                reason=str(reason or ""),
                ip=_client_ip(request),
                user_agent=request.headers.get("user-agent"),
            ),
        ),
    )


@router.get(
    "/thresholds",
    response_model=ControlRoomThresholdsResponse,
    dependencies=[Depends(require_permission("operations.read"))],
)
async def control_room_thresholds(user: dict = Depends(require_authenticated)):
    return project_public_control_room_response(
        ControlRoomThresholdsResponse,
        await control_room_service.list_thresholds(user),
    )


@router.post(
    "/thresholds",
    response_model=ControlRoomThresholdMutationResponse,
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
)
async def control_room_upsert_threshold(
    request: Request,
    body: ControlRoomThresholdRequest = Body(
        default_factory=ControlRoomThresholdRequest
    ),
    user: dict = Depends(require_authenticated),
):
    return project_threshold_mutation_response(
        await _invalidate_after_write(
            user,
            control_room_service.upsert_threshold(
                body.model_dump(exclude_unset=True),
                user,
                ip=_client_ip(request),
                user_agent=request.headers.get("user-agent"),
            ),
        ),
    )


@router.patch(
    "/thresholds",
    response_model=ControlRoomThresholdMutationResponse,
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
)
async def control_room_patch_threshold(
    request: Request,
    body: ControlRoomThresholdRequest = Body(
        default_factory=ControlRoomThresholdRequest
    ),
    user: dict = Depends(require_authenticated),
):
    return project_threshold_mutation_response(
        await _invalidate_after_write(
            user,
            control_room_service.upsert_threshold(
                body.model_dump(exclude_unset=True),
                user,
                ip=_client_ip(request),
                user_agent=request.headers.get("user-agent"),
            ),
        ),
    )


@router.get(
    "/lessons",
    response_model=ControlRoomLessonsResponse,
    dependencies=[Depends(require_permission("datasets.read"))],
)
async def control_room_lessons(
    cartridge_id: str | None = Query(default=None),
    anomaly_type: str | None = Query(default=None),
    item_id: str | None = Query(default=None),
    user: dict = Depends(require_authenticated),
):
    return project_public_control_room_response(
        ControlRoomLessonsResponse,
        await control_room_service.list_lessons(
            user,
            cartridge_id=cartridge_id,
            anomaly_type=anomaly_type,
            item_id=item_id,
        ),
    )
