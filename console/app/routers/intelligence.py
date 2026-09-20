from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time
from contextlib import asynccontextmanager
from typing import Any, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.dependencies import require_authenticated
from app.schemas.control_room_talent_responses import (
    ControlRoomTalentAnomaliesResponse,
    ControlRoomTalentOverviewResponse,
)
from app.services import auth, intelligence_engine
from app.services.auth import verify_internal_api_key
from app.services.intelligence import backtesting as intelligence_backtesting
from app.services.intelligence import bigquery_shadow
from app.services.intelligence import calibration_service
from app.services.intelligence import decision_orchestrator
from app.services.intelligence import engine_policy
from app.services.intelligence import history as intelligence_history
from app.services.intelligence import monte_carlo
from app.services.intelligence import monte_carlo_service
from app.services.intelligence import orchestrator_execution
from app.services.intelligence.outcome_binding import (
    assert_expected_pipeline_run,
    persist_gold_refresh_binding,
)
from app.services.intelligence.publication_trace import capture_publication_trace
from app.services.intelligence.readiness import intelligence_readiness
from app.services.csrf import require_csrf
from app.services.db_scope import scoped_db_for_user
from app.services.permissions import require_permission
from app.services.security_context import verify_signed_security_context


router = APIRouter(prefix="/api/intelligence", tags=["Intelligence"])
v1_router = APIRouter(prefix="/api/v1/intelligence", tags=["Intelligence"])
internal_router = APIRouter(
    prefix="/internal/intelligence", tags=["Intelligence (internal)"]
)
DATASETS_READ_DEPENDENCY = [Depends(require_permission("datasets.read"))]


def _require_math_engines_enabled() -> None:
    if not engine_policy.math_engines_enabled():
        raise HTTPException(status_code=403, detail=engine_policy.PAUSED_REASON)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IntelligenceRunRequest(_StrictModel):
    cartridge_id: str | None = Field(
        default=None, pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,63}$"
    )
    metrics: list[str] | None = None
    include_external: bool = False
    horizon_days: int | list[int] | None = None
    dry_run: bool = False
    run_mode: Literal["manual", "scheduled", "backtest", "smoke"] | None = None

    @field_validator("metrics")
    @classmethod
    def _validate_metrics(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return value
        cleaned = [str(item).strip() for item in value if str(item or "").strip()]
        if len(cleaned) > 20:
            raise ValueError("metrics cannot contain more than 20 entries")
        for item in cleaned:
            if len(item) > 128 or not item.replace("_", "").replace("-", "").isalnum():
                raise ValueError("metrics entries must be simple identifiers")
        return cleaned

    @field_validator("horizon_days")
    @classmethod
    def _validate_horizon_days(
        cls, value: int | list[int] | None
    ) -> int | list[int] | None:
        if value is None:
            return value
        values = value if isinstance(value, list) else [value]
        if not values:
            return []
        for item in values:
            if int(item) < 1 or int(item) > 90:
                raise ValueError("horizon_days must be between 1 and 90")
        return value


class GoldRefreshIntelligenceRequest(_StrictModel):
    tenant_id: str = Field(min_length=1, max_length=80)
    workspace_id: str = Field(min_length=1, max_length=80)
    cartridge_id: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
    airflow_dag_run_id: str = Field(min_length=1, max_length=256)
    pipeline_run_id: str = Field(min_length=1, max_length=512)
    materialization_status: Literal["success", "partial"] = "success"
    datasets: list[str] = Field(default_factory=list, max_length=200)
    finished_at: str | None = Field(default=None, max_length=80)

    @field_validator("datasets")
    @classmethod
    def _validate_datasets(cls, value: list[str]) -> list[str]:
        cleaned = [str(item).strip() for item in value if str(item or "").strip()]
        for item in cleaned:
            if len(item) > 128 or not item.replace("_", "").replace("-", "").isalnum():
                raise ValueError("datasets entries must be simple identifiers")
        return sorted(set(cleaned))


class BigQueryTalent9BoxBaselineRequest(_StrictModel):
    tenant_id: str = Field(
        pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
    )
    workspace_id: str = Field(
        pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
    )
    cartridge_id: Literal["sap_successfactors"]
    dataset: Literal["sap_successfactors_talent_9box"]
    pipeline_run_id: str = Field(min_length=1, max_length=512)
    source_run_id: str = Field(
        pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
    )


class InternalMcpRequest(_StrictModel):
    security_context: dict[str, Any]
    payload: dict[str, Any] = Field(default_factory=dict)
    effect_authority: dict[str, Any] | None = None

    @field_validator("payload")
    @classmethod
    def _validate_payload_scope(cls, value: dict[str, Any]) -> dict[str, Any]:
        _reject_scope_fields(value)
        return value


class InternalMcpDecisionRequest(InternalMcpRequest):
    execute_engines: bool = False
    engine_inputs: dict[str, Any] = Field(default_factory=dict)

    @field_validator("engine_inputs")
    @classmethod
    def _validate_engine_scope(cls, value: dict[str, Any]) -> dict[str, Any]:
        _reject_scope_fields(value)
        return value


class InternalMcpWisdomBitRequest(_StrictModel):
    security_context: dict[str, Any]
    wisdom_bit_id: str = Field(min_length=1, max_length=120)
    cartridge_id: str = Field(default="sap_successfactors", max_length=120)
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("payload")
    @classmethod
    def _validate_wisdom_payload_scope(cls, value: dict[str, Any]) -> dict[str, Any]:
        _reject_scope_fields(value)
        return value


class ExternalSourcePatchRequest(_StrictModel):
    enabled: bool | None = None
    ttl_seconds: int | None = Field(default=None, ge=60, le=2_592_000)
    config: dict | None = None
    metadata: dict | None = None


class ExternalRunRequest(_StrictModel):
    source_ids: list[str] | None = None
    dry_run: bool = False


class OutcomeRequest(_StrictModel):
    option_id: str | None = None
    action_taken: str = Field(min_length=1, max_length=500)
    actual_value: float | None = None
    predicted_value: float | None = None
    outcome_summary: str | None = Field(default=None, max_length=2_000)
    learned_rule: str | None = Field(default=None, max_length=2_000)


class BacktestRunRequest(_StrictModel):
    source_system: Literal["replicon"] = "replicon"
    source_dataset: str | None = Field(
        default=None, pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,127}$"
    )
    metric: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
    start_period: str | None = Field(default=None, max_length=32)
    end_period: str | None = Field(default=None, max_length=32)
    mode: Literal["historical_replay", "outcome_linked", "fixture_validation"] = (
        "historical_replay"
    )
    labels_required: int = Field(default=10, ge=1, le=10_000)
    prediction_threshold: float | None = Field(default=None, ge=0, le=1)
    historical_label_robust_z: float | None = Field(default=None, ge=0, le=100)
    limit: int = Field(default=5000, ge=1, le=10_000)
    result_limit: int = Field(default=100, ge=0, le=1000)


def _validate_discrete_value_sizes(value: dict[str, dict] | None) -> None:
    for name, spec in (value or {}).items():
        if not isinstance(spec, dict):
            continue
        raw_values = spec.get("values")
        if (
            isinstance(raw_values, list)
            and len(raw_values) > monte_carlo.MAX_DISCRETE_VALUES
        ):
            raise ValueError(
                f"{name}.values cannot contain more than "
                f"{monte_carlo.MAX_DISCRETE_VALUES} entries"
            )


class MonteCarloOptionRequest(_StrictModel):
    option_id: str = Field(min_length=1, max_length=120)
    label: str | None = Field(default=None, max_length=240)
    input_variables: dict[str, dict] | None = None
    assumptions: dict | None = None

    @field_validator("input_variables")
    @classmethod
    def _validate_option_values(
        cls, value: dict[str, dict] | None
    ) -> dict[str, dict] | None:
        _validate_discrete_value_sizes(value)
        return value


class MonteCarloRunRequest(_StrictModel):
    source_type: Literal[
        "signal", "decision_option", "manual_fixture", "backtest_case", "wisdom_bit"
    ]
    source_id: str = Field(min_length=1, max_length=256)
    horizon_days: int = Field(default=30, ge=1, le=365)
    iterations: int = Field(default=1000, ge=1, le=10_000)
    seed: int = Field(default=0, ge=0, le=monte_carlo.MAX_SEED)
    input_variables: dict[str, dict] = Field(default_factory=dict)
    assumptions: dict = Field(default_factory=dict)
    use_external_market_context: bool = False
    output_metric: Literal["net_value", "delta", "cost", "delay_days"] = "net_value"
    breach_threshold: float | None = None
    breach_direction: Literal["below", "above"] | None = None
    evidence_refs: list[dict] = Field(default_factory=list, max_length=20)
    options: list[MonteCarloOptionRequest] | None = Field(default=None, max_length=10)

    @field_validator("input_variables")
    @classmethod
    def _validate_input_variables(cls, value: dict[str, dict]) -> dict[str, dict]:
        if not isinstance(value, dict) or not value:
            raise ValueError("input_variables must be a non-empty object")
        if len(value) > 50:
            raise ValueError("input_variables cannot contain more than 50 entries")
        forbidden = {"tenant_id", "workspace_id", "security_context"}
        if forbidden & set(value):
            raise ValueError("scope variables are not accepted")
        _validate_discrete_value_sizes(value)
        return value


class CalibrationObservationRequest(_StrictModel):
    source_type: Literal[
        "monte_carlo_simulation",
        "decision_option",
        "prediction_outcome",
        "backtest_case",
        "manual_fixture",
    ]
    source_id: str = Field(min_length=1, max_length=256)
    predicted_metric: str | None = Field(default=None, min_length=1, max_length=120)
    predicted_probability: float | None = Field(default=None, ge=0, le=1)
    predicted_value: float | None = None
    predicted_interval: dict | None = None
    actual_status: Literal["hit", "miss", "partial", "unknown"] | None = None
    actual_value: float | None = None
    observed_at: str | None = Field(default=None, max_length=80)
    horizon_days: int = Field(default=30, ge=1, le=3650)
    model_version: str | None = Field(default=None, max_length=120)
    calibration_group: str | None = Field(default=None, max_length=80)
    evidence_refs: list[dict] = Field(default_factory=list, max_length=20)


class CalibrationRecomputeRequest(_StrictModel):
    calibration_group: str = Field(min_length=1, max_length=80)
    model_version: str | None = Field(default=None, max_length=120)
    source_type: (
        Literal[
            "monte_carlo_simulation",
            "decision_option",
            "prediction_outcome",
            "backtest_case",
            "manual_fixture",
        ]
        | None
    ) = None
    source_id: str | None = Field(default=None, max_length=256)
    limit: int = Field(default=5000, ge=1, le=10_000)


class CalibrationStateRequest(_StrictModel):
    calibration_group: str | None = Field(default=None, max_length=80)
    model_version: str | None = Field(default=None, max_length=120)
    limit: int = Field(default=10, ge=1, le=25)


class OrchestrationRequest(_StrictModel):
    source_type: Literal[
        "control_room_item",
        "agent_alert",
        "intelligence_signal",
        "monte_carlo_simulation",
        "calibration_observation",
        "wisdom_bit",
        "manual_fixture",
    ]
    source_id: str = Field(min_length=1, max_length=256)
    title: str | None = Field(default=None, max_length=500)
    description: str | None = Field(default=None, max_length=4000)
    metrics: dict[str, Any] = Field(default_factory=dict)
    entities: list[dict[str, Any]] = Field(default_factory=list, max_length=50)
    time_horizon: str | None = Field(default=None, max_length=120)
    constraints: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list, max_length=20)

    @field_validator("metrics", "constraints")
    @classmethod
    def _validate_no_scope_dict(cls, value: dict[str, Any]) -> dict[str, Any]:
        _reject_scope_fields(value)
        return value

    @field_validator("entities", "evidence_refs")
    @classmethod
    def _validate_no_scope_list(
        cls,
        value: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        _reject_scope_fields(value)
        return value


class OrchestrationExecuteEnginesRequest(_StrictModel):
    engine_inputs: dict[str, Any] = Field(default_factory=dict)

    @field_validator("engine_inputs")
    @classmethod
    def _validate_engine_inputs(cls, value: dict[str, Any]) -> dict[str, Any]:
        _reject_scope_fields(value)
        return value


def _payload(model: BaseModel | None) -> dict:
    if model is None:
        return {}
    return model.model_dump(exclude_none=True)


def _reject_scope_fields(value: Any) -> None:
    if isinstance(value, dict):
        forbidden = {"tenant_id", "workspace_id", "security_context"}
        overlap = forbidden & {str(key) for key in value}
        if overlap:
            raise ValueError("scope variables are not accepted")
        for item in value.values():
            _reject_scope_fields(item)
    elif isinstance(value, list):
        for item in value:
            _reject_scope_fields(item)


def _orchestrator_error(exc: Any) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


def _invalidate_control_room_cache(user: dict) -> None:
    try:
        from app.routers.control_room import _control_room_cache_invalidate
    except Exception:
        return
    _control_room_cache_invalidate(user)


def _gold_refresh_run_ref(body: GoldRefreshIntelligenceRequest) -> str:
    return (
        "gold-refresh:"
        f"{body.workspace_id}:"
        f"{body.cartridge_id}:"
        f"{body.airflow_dag_run_id}"
    )


async def _resolve_gold_refresh_scope(
    body: GoldRefreshIntelligenceRequest,
) -> dict[str, str | bool]:
    pool = await auth.pool()
    row = await pool.fetchrow(
        """
        SELECT t.id::text AS tenant_id, w.id::text AS workspace_id
          FROM workspaces w
          JOIN tenants t ON t.id = w.tenant_id
         WHERE w.id::text = $1
        """,
        body.workspace_id,
    )
    if not row:
        raise HTTPException(
            status_code=400,
            detail=f"workspace not found for gold refresh: {body.workspace_id}",
        )
    tenant_id = str(row["tenant_id"])
    return {
        "tenant_id": tenant_id,
        "workspace_id": str(row["workspace_id"]),
        "tenant_mismatch": tenant_id != str(body.tenant_id),
        "requested_tenant_id": str(body.tenant_id),
    }


async def _gold_refresh_user(body: GoldRefreshIntelligenceRequest) -> dict[str, Any]:
    scope = await _resolve_gold_refresh_scope(body)
    tenant_id = str(scope["tenant_id"])
    workspace_id = str(scope["workspace_id"])
    return {
        "id": 0,
        "email": "airflow@internal",
        "role": "admin",
        "workspace_role": "workspace_admin",
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "active_tenant_id": tenant_id,
        "active_workspace_id": workspace_id,
        "allowed_cartridges": [body.cartridge_id],
        "gold_refresh_requested_tenant_id": scope["requested_tenant_id"],
        "gold_refresh_tenant_mismatch": bool(scope["tenant_mismatch"]),
    }


def _internal_mcp_user(
    body: InternalMcpRequest | InternalMcpWisdomBitRequest,
    internal_service: str,
    *,
    permission: str = "control_room.write",
) -> dict[str, Any]:
    if internal_service != "mcp-infra":
        raise HTTPException(
            status_code=403, detail="only mcp-infra can use this internal route"
        )
    try:
        ctx = verify_signed_security_context(body.security_context)
    except Exception as exc:
        raise HTTPException(
            status_code=403, detail=f"invalid security_context: {exc}"
        ) from exc
    if not ctx.get("trusted"):
        raise HTTPException(status_code=403, detail="trusted security_context required")
    permissions = {str(item) for item in (ctx.get("permissions") or [])}
    if permission not in permissions:
        raise HTTPException(
            status_code=403, detail=f"permission required: {permission}"
        )
    tenant_id = str(ctx.get("tenant_id") or "").strip()
    workspace_id = str(ctx.get("workspace_id") or "").strip()
    if not tenant_id or not workspace_id:
        raise HTTPException(status_code=403, detail="tenant/workspace scope required")
    return {
        "id": ctx.get("user_id") or 0,
        "email": ctx.get("email") or "agent-runner@omega.local",
        "role": ctx.get("role") or "agent",
        "workspace_role": ctx.get("workspace_role"),
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "active_tenant_id": tenant_id,
        "active_workspace_id": workspace_id,
        "allowed_cartridges": list(ctx.get("allowed_cartridges") or []),
        "agent_id": ctx.get("agent_id"),
        "agent_slug": ctx.get("agent_slug"),
        "agent_run_id": ctx.get("agent_run_id"),
        "security_context_source": ctx.get("source"),
    }


_SCHEDULED_EFFECT_FIELDS = {
    "source",
    "audience",
    "purpose",
    "tool",
    "schedule_run_id",
    "fencing_token",
    "tenant_id",
    "workspace_id",
    "agent_id",
    "agent_run_id",
    "jti",
    "body_digest",
    "_signed_at",
    "_signature_version",
    "_signature",
}
_SCHEDULED_EFFECT_HEX = re.compile(r"^[0-9a-f]{64}$")


def _verified_effect_authority(
    user: dict[str, Any], authority: object, expected_tool: str
) -> dict[str, Any]:
    if not isinstance(authority, dict) or set(authority) != _SCHEDULED_EFFECT_FIELDS:
        raise HTTPException(403, "scheduled effect authority is invalid")
    fixed = {
        "source": "console",
        "audience": "mcp-infra",
        "purpose": "mcp.scheduled_effect",
        "tool": expected_tool,
        "tenant_id": user.get("tenant_id"),
        "workspace_id": user.get("workspace_id"),
        "agent_id": user.get("agent_id"),
        "agent_run_id": user.get("agent_run_id"),
        "_signature_version": "hmac-sha256-v2",
    }
    if any(authority.get(key) != value for key, value in fixed.items()):
        raise HTTPException(403, "scheduled effect authority is invalid")
    try:
        schedule_run_id = int(authority["schedule_run_id"])
        fencing_token = int(authority["fencing_token"])
        signed_at = int(authority["_signed_at"])
    except (TypeError, ValueError) as exc:
        raise HTTPException(403, "scheduled effect authority is invalid") from exc
    if (
        isinstance(authority["schedule_run_id"], bool)
        or isinstance(authority["fencing_token"], bool)
        or schedule_run_id <= 0
        or fencing_token <= 0
    ):
        raise HTTPException(403, "scheduled effect authority is invalid")
    if any(
        not _SCHEDULED_EFFECT_HEX.fullmatch(str(authority.get(field) or ""))
        for field in ("jti", "body_digest", "_signature")
    ):
        raise HTTPException(403, "scheduled effect authority is invalid")
    now = int(time.time())
    if signed_at > now + 30 or now - signed_at > 300:
        raise HTTPException(403, "scheduled effect authority is expired")
    key = str(os.environ.get("SECURITY_CONTEXT_SIGNING_KEY") or "").strip()
    if len(key) < 32:
        raise HTTPException(503, "scheduled effect authority is unavailable")
    unsigned = {key: value for key, value in authority.items() if key != "_signature"}
    canonical = json.dumps(
        unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    expected = hmac.new(key.encode(), canonical, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(str(authority["_signature"]), expected):
        raise HTTPException(403, "scheduled effect authority is invalid")
    return {
        "schedule_run_id": schedule_run_id,
        "fencing_token": fencing_token,
        "agent_id": str(user.get("agent_id") or ""),
    }


@asynccontextmanager
async def _scheduled_effect_guard(
    user: dict[str, Any], authority: object, expected_tool: str
):
    scheduled = user.get("security_context_source") == "agent_runner"
    if not scheduled:
        if authority is not None:
            raise HTTPException(403, "scheduled effect authority is not permitted")
        yield
        return
    if authority is None:
        raise HTTPException(403, "scheduled effect authority is required")
    user["_scheduled_effect_authority"] = _verified_effect_authority(
        user, authority, expected_tool
    )
    try:
        pool = await auth.pool()
        async with scoped_db_for_user(pool, user) as (conn, _tenant_id, _workspace_id):
            user["_scheduled_effect_connection"] = conn
            try:
                yield
            finally:
                user.pop("_scheduled_effect_connection", None)
    finally:
        user.pop("_scheduled_effect_authority", None)


@internal_router.post("/gold-refresh")
async def intelligence_gold_refresh_internal(
    body: GoldRefreshIntelligenceRequest,
    internal_service: str = Depends(verify_internal_api_key),
):
    if internal_service != "airflow":
        raise HTTPException(
            status_code=403, detail="only airflow can trigger Gold refresh intelligence"
        )
    user = await _gold_refresh_user(body)
    run_ref = _gold_refresh_run_ref(body)
    try:
        await assert_expected_pipeline_run(
            user,
            expected_run_id=body.pipeline_run_id,
            cartridge_id=body.cartridge_id,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail="Gold intelligence outcome unavailable"
        ) from exc
    payload = {
        "cartridge_id": body.cartridge_id,
        "datasets": body.datasets,
        "include_external": False,
        "dry_run": False,
        "run_mode": "gold_refresh",
        "run_ref": run_ref,
        "horizon_days": [7, 21],
        "metadata": {
            "trigger": "gold_refresh",
            "pipeline_run_id": body.pipeline_run_id,
            "airflow_dag_run_id": body.airflow_dag_run_id,
            "materialization_status": body.materialization_status,
            "datasets_received": body.datasets,
            "finished_at": body.finished_at,
            "requested_tenant_id": body.tenant_id,
            "canonical_tenant_id": user["tenant_id"],
            "tenant_mismatch": user.get("gold_refresh_tenant_mismatch", False),
        },
    }
    try:
        with capture_publication_trace() as publication_trace:
            result = await intelligence_engine.run_intelligence(
                user, payload, persist=True
            )
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail="Gold intelligence outcome unavailable"
        ) from exc
    status = result.get("status") or (
        "completed" if result.get("signals") else "not_ready"
    )
    signal_count = (
        int(result.get("signals_generated") or 0)
        if result.get("idempotent")
        else len(result.get("signals") or [])
    )
    if status not in {"binding_pending", "completed"} or signal_count <= 0:
        raise HTTPException(
            status_code=503, detail="Gold intelligence outcome unavailable"
        )
    try:
        await persist_gold_refresh_binding(
            user,
            expected_run_id=body.pipeline_run_id,
            expected_run_ref=run_ref,
            intelligence_result={**result, "run_ref": result.get("run_ref") or run_ref},
            publication_trace=publication_trace,
            expected_datasets=body.datasets,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail="Gold intelligence outcome unavailable"
        ) from exc
    status = "completed"
    _invalidate_control_room_cache(user)
    return {
        "ok": True,
        "run_ref": result.get("run_ref") or run_ref,
        "intelligence_run_id": result.get("intelligence_run_id"),
        "status": status,
        "idempotent": bool(result.get("idempotent")),
        "signals": signal_count,
        "skipped": len(result.get("skipped") or []),
        "dataset_unavailable_count": result.get("dataset_unavailable_count", 0),
        "insufficient_history_count": result.get("insufficient_history_count", 0),
        "skipped_counts": result.get("skipped_counts") or {},
    }


@internal_router.post("/bigquery-shadow/talent-9box-baseline")
async def intelligence_bigquery_talent_9box_baseline_internal(
    body: BigQueryTalent9BoxBaselineRequest,
    internal_service: str = Depends(verify_internal_api_key),
):
    if internal_service != "airflow":
        raise HTTPException(
            status_code=403,
            detail="only airflow can request the BigQuery Talent shadow baseline",
        )
    return await bigquery_shadow.build_talent_9box_baseline(
        **body.model_dump()
    )


@internal_router.post("/monte-carlo/run")
async def intelligence_monte_carlo_run_internal(
    body: InternalMcpRequest,
    internal_service: str = Depends(verify_internal_api_key),
):
    _require_math_engines_enabled()
    user = _internal_mcp_user(body, internal_service)
    request = MonteCarloRunRequest.model_validate(body.payload)
    async with _scheduled_effect_guard(
        user, body.effect_authority, "simulation__monte_carlo_run"
    ):
        return await monte_carlo_service.run_simulation(user, _payload(request))


@internal_router.post("/orchestrate")
async def intelligence_orchestrate_internal(
    body: InternalMcpDecisionRequest,
    internal_service: str = Depends(verify_internal_api_key),
):
    if body.execute_engines:
        _require_math_engines_enabled()
    user = _internal_mcp_user(body, internal_service)
    if (
        str(user.get("agent_slug") or "")
        == "sap_successfactors_talent_monitor"
        and user.get("security_context_source") != "agent_runner"
    ):
        raise HTTPException(
            status_code=403,
            detail="manual Talent decision orchestration is paused by server policy",
        )
    request = OrchestrationRequest.model_validate(body.payload)
    try:
        async with _scheduled_effect_guard(
            user, body.effect_authority, "decision__orchestrate"
        ):
            orchestration = await decision_orchestrator.orchestrate(
                user, _payload(request)
            )
            if not body.execute_engines:
                return orchestration
            orchestration_row = (
                orchestration.get("orchestration")
                if isinstance(orchestration, dict)
                else {}
            )
            orchestration_id = str(
                (orchestration_row or {}).get("orchestration_id") or ""
            )
            if not orchestration_id:
                raise HTTPException(status_code=500, detail="orchestration_id missing")
            execution_request = OrchestrationExecuteEnginesRequest.model_validate(
                {"engine_inputs": body.engine_inputs}
            )
            execution = await orchestrator_execution.execute_engines(
                user,
                orchestration_id,
                _payload(execution_request),
            )
            return {**orchestration, "engine_execution": execution}
    except decision_orchestrator.DecisionOrchestratorError as exc:
        raise _orchestrator_error(exc) from exc
    except orchestrator_execution.OrchestratorExecutionError as exc:
        raise _orchestrator_error(exc) from exc


@internal_router.post("/wisdom-bits/run")
async def intelligence_wisdom_bits_run_internal(
    body: InternalMcpWisdomBitRequest,
    internal_service: str = Depends(verify_internal_api_key),
):
    user = _internal_mcp_user(body, internal_service)
    wisdom_bit_id = body.wisdom_bit_id.strip().upper()
    if wisdom_bit_id != "WB-TALENTO":
        raise HTTPException(status_code=404, detail="wisdom_bit_id is not available")
    if body.cartridge_id not in {"sap_successfactors", "sap-successfactors"}:
        raise HTTPException(
            status_code=400, detail="WB-TALENTO belongs to sap_successfactors"
        )

    from app.services import control_room_service

    overview = ControlRoomTalentOverviewResponse.project(
        await control_room_service.sap_successfactors_talent_overview(user)
    ).model_dump(mode="json", exclude_none=True)
    anomalies = ControlRoomTalentAnomaliesResponse.project(
        await control_room_service.sap_successfactors_talent_anomalies(user)
    ).model_dump(mode="json", exclude_none=True)
    readiness = overview.get("readiness") or {}
    if not isinstance(readiness, dict):
        readiness = {}
    signal_items = anomalies.get("items") or []
    if not isinstance(signal_items, list):
        signal_items = []
    blockers = list(overview.get("blockers") or [])
    return {
        "ok": True,
        "wisdom_bit_id": "WB-TALENTO",
        "cartridge_id": "sap_successfactors",
        "decision_mode": "recommendation_only",
        "writeback_enabled": False,
        "compensation_enabled": False,
        "status": readiness.get("status") or "partial",
        "generated_at": overview.get("generated_at"),
        "profile": overview.get("profile") or {},
        "coverage": readiness,
        "blockers": blockers,
        "signals": {
            "count": len(signal_items),
            "items": signal_items[:10],
        },
        "evidence": {"recommendation_only": True},
    }


@internal_router.post("/calibration/state")
async def intelligence_calibration_state_internal(
    body: InternalMcpRequest,
    internal_service: str = Depends(verify_internal_api_key),
):
    user = _internal_mcp_user(body, internal_service, permission="datasets.read")
    request = CalibrationStateRequest.model_validate(body.payload)
    return await calibration_service.get_state(user, **_payload(request))


@router.post(
    "/orchestrate",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("control_room.write")),
    ],
)
@v1_router.post(
    "/orchestrate",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("control_room.write")),
    ],
)
async def intelligence_orchestrate(
    body: OrchestrationRequest,
    user: dict = Depends(require_authenticated),
):
    try:
        return await decision_orchestrator.orchestrate(user, _payload(body))
    except decision_orchestrator.DecisionOrchestratorError as exc:
        raise _orchestrator_error(exc) from exc


@router.get("/orchestrate", dependencies=[Depends(require_permission("datasets.read"))])
@v1_router.get(
    "/orchestrate", dependencies=[Depends(require_permission("datasets.read"))]
)
async def intelligence_orchestration_list(
    source_type: Literal[
        "control_room_item",
        "agent_alert",
        "intelligence_signal",
        "monte_carlo_simulation",
        "calibration_observation",
        "wisdom_bit",
        "manual_fixture",
    ]
    | None = Query(default=None),
    source_id: str | None = Query(default=None, max_length=256),
    limit: int = Query(default=50, ge=1, le=250),
    user: dict = Depends(require_authenticated),
):
    try:
        return await decision_orchestrator.list_orchestrations(
            user,
            source_type=source_type,
            source_id=source_id,
            limit=limit,
        )
    except decision_orchestrator.DecisionOrchestratorError as exc:
        raise _orchestrator_error(exc) from exc


@router.get(
    "/orchestrate/{orchestration_id}",
    dependencies=[Depends(require_permission("datasets.read"))],
)
@v1_router.get(
    "/orchestrate/{orchestration_id}",
    dependencies=[Depends(require_permission("datasets.read"))],
)
async def intelligence_orchestration_detail(
    orchestration_id: str,
    user: dict = Depends(require_authenticated),
):
    try:
        return await decision_orchestrator.get_orchestration(user, orchestration_id)
    except decision_orchestrator.DecisionOrchestratorError as exc:
        raise _orchestrator_error(exc) from exc


@router.post(
    "/orchestrate/{orchestration_id}/execute-engines",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("control_room.write")),
    ],
)
@v1_router.post(
    "/orchestrate/{orchestration_id}/execute-engines",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("control_room.write")),
    ],
)
async def intelligence_orchestration_execute_engines(
    orchestration_id: str,
    body: OrchestrationExecuteEnginesRequest | None = Body(default=None),
    user: dict = Depends(require_authenticated),
):
    _require_math_engines_enabled()
    try:
        return await orchestrator_execution.execute_engines(
            user,
            orchestration_id,
            _payload(body),
        )
    except orchestrator_execution.OrchestratorExecutionError as exc:
        raise _orchestrator_error(exc) from exc


@router.get(
    "/orchestrate/{orchestration_id}/executions",
    dependencies=[Depends(require_permission("datasets.read"))],
)
@v1_router.get(
    "/orchestrate/{orchestration_id}/executions",
    dependencies=[Depends(require_permission("datasets.read"))],
)
async def intelligence_orchestration_executions(
    orchestration_id: str,
    user: dict = Depends(require_authenticated),
):
    try:
        return await orchestrator_execution.list_executions(user, orchestration_id)
    except orchestrator_execution.OrchestratorExecutionError as exc:
        raise _orchestrator_error(exc) from exc


@router.get("/signals", dependencies=[Depends(require_permission("datasets.read"))])
@v1_router.get("/signals", dependencies=[Depends(require_permission("datasets.read"))])
async def intelligence_signals(
    limit: int = Query(default=100, ge=1, le=500),
    user: dict = Depends(require_authenticated),
):
    return await intelligence_engine.list_signals(user, limit=limit)


@router.get(
    "/signals/{signal_id}", dependencies=[Depends(require_permission("datasets.read"))]
)
@v1_router.get(
    "/signals/{signal_id}", dependencies=[Depends(require_permission("datasets.read"))]
)
async def intelligence_signal_detail(
    signal_id: str,
    user: dict = Depends(require_authenticated),
):
    return await intelligence_engine.get_signal(user, signal_id)


@router.get("/readiness", dependencies=[Depends(require_permission("datasets.read"))])
@v1_router.get(
    "/readiness", dependencies=[Depends(require_permission("datasets.read"))]
)
async def intelligence_readiness_endpoint(
    require_data: bool = Query(default=True),
    user: dict = Depends(require_authenticated),
):
    return await intelligence_readiness(user, require_data=require_data)


@router.get("/runs", dependencies=[Depends(require_permission("datasets.read"))])
@v1_router.get("/runs", dependencies=[Depends(require_permission("datasets.read"))])
async def intelligence_runs(
    limit: int = Query(default=50, ge=1, le=250),
    user: dict = Depends(require_authenticated),
):
    return await intelligence_history.list_runs(user, limit=limit)


@router.get(
    "/runs/{run_id}", dependencies=[Depends(require_permission("datasets.read"))]
)
@v1_router.get(
    "/runs/{run_id}", dependencies=[Depends(require_permission("datasets.read"))]
)
async def intelligence_run_detail(
    run_id: str,
    user: dict = Depends(require_authenticated),
):
    return await intelligence_history.get_run(user, run_id)


@router.get("/history", dependencies=[Depends(require_permission("datasets.read"))])
@v1_router.get("/history", dependencies=[Depends(require_permission("datasets.read"))])
async def intelligence_history_endpoint(
    limit: int = Query(default=100, ge=1, le=500),
    user: dict = Depends(require_authenticated),
):
    return await intelligence_history.list_history(user, limit=limit)


@router.get("/calibration", dependencies=[Depends(require_permission("datasets.read"))])
@v1_router.get(
    "/calibration", dependencies=[Depends(require_permission("datasets.read"))]
)
async def intelligence_calibration_endpoint(
    min_outcomes_required: int = Query(default=10, ge=1, le=1000),
    user: dict = Depends(require_authenticated),
):
    return await intelligence_history.calibration_report(
        user,
        min_outcomes_required=min_outcomes_required,
    )


@router.post(
    "/calibration/observe",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("control_room.write")),
    ],
)
@v1_router.post(
    "/calibration/observe",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("control_room.write")),
    ],
)
async def intelligence_calibration_observe(
    body: CalibrationObservationRequest,
    user: dict = Depends(require_authenticated),
):
    _require_math_engines_enabled()
    return await calibration_service.observe(user, _payload(body))


@router.post(
    "/calibration/recompute",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("control_room.write")),
    ],
)
@v1_router.post(
    "/calibration/recompute",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("control_room.write")),
    ],
)
async def intelligence_calibration_recompute(
    body: CalibrationRecomputeRequest,
    user: dict = Depends(require_authenticated),
):
    _require_math_engines_enabled()
    return await calibration_service.recompute(user, _payload(body))


@router.get(
    "/calibration/state",
    dependencies=[Depends(require_permission("datasets.read"))],
)
@v1_router.get(
    "/calibration/state",
    dependencies=[Depends(require_permission("datasets.read"))],
)
async def intelligence_calibration_state(
    calibration_group: str | None = Query(default=None, max_length=80),
    model_version: str | None = Query(default=None, max_length=120),
    limit: int = Query(default=50, ge=1, le=250),
    user: dict = Depends(require_authenticated),
):
    return await calibration_service.get_state(
        user,
        calibration_group=calibration_group,
        model_version=model_version,
        limit=limit,
    )


@router.get(
    "/calibration/observations",
    dependencies=[Depends(require_permission("datasets.read"))],
)
@v1_router.get(
    "/calibration/observations",
    dependencies=[Depends(require_permission("datasets.read"))],
)
async def intelligence_calibration_observations(
    source_type: Literal[
        "monte_carlo_simulation",
        "decision_option",
        "prediction_outcome",
        "backtest_case",
        "manual_fixture",
    ]
    | None = Query(default=None),
    source_id: str | None = Query(default=None, max_length=256),
    calibration_group: str | None = Query(default=None, max_length=80),
    model_version: str | None = Query(default=None, max_length=120),
    limit: int = Query(default=50, ge=1, le=250),
    user: dict = Depends(require_authenticated),
):
    return await calibration_service.list_observations(
        user,
        source_type=source_type,
        source_id=source_id,
        calibration_group=calibration_group,
        model_version=model_version,
        limit=limit,
    )


@router.get("/backtests", dependencies=[Depends(require_permission("datasets.read"))])
@v1_router.get(
    "/backtests", dependencies=[Depends(require_permission("datasets.read"))]
)
async def intelligence_backtests(
    limit: int = Query(default=50, ge=1, le=250),
    user: dict = Depends(require_authenticated),
):
    return await intelligence_backtesting.list_backtests(user, limit=limit)


@router.post(
    "/backtests/run",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("control_room.write")),
    ],
)
@v1_router.post(
    "/backtests/run",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("control_room.write")),
    ],
)
async def intelligence_backtests_run(
    body: BacktestRunRequest,
    user: dict = Depends(require_authenticated),
):
    return await intelligence_backtesting.run_backtest(user, _payload(body))


@router.get(
    "/backtests/{backtest_id}",
    dependencies=[Depends(require_permission("datasets.read"))],
)
@v1_router.get(
    "/backtests/{backtest_id}",
    dependencies=[Depends(require_permission("datasets.read"))],
)
async def intelligence_backtest_detail(
    backtest_id: str,
    user: dict = Depends(require_authenticated),
):
    return await intelligence_backtesting.get_backtest(user, backtest_id)


@router.get(
    "/backtests/{backtest_id}/results",
    dependencies=[Depends(require_permission("datasets.read"))],
)
@v1_router.get(
    "/backtests/{backtest_id}/results",
    dependencies=[Depends(require_permission("datasets.read"))],
)
async def intelligence_backtest_results(
    backtest_id: str,
    limit: int = Query(default=500, ge=1, le=5000),
    user: dict = Depends(require_authenticated),
):
    return await intelligence_backtesting.get_backtest_results(
        user,
        backtest_id,
        limit=limit,
    )


@router.post(
    "/monte-carlo/run",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("control_room.write")),
    ],
)
@v1_router.post(
    "/monte-carlo/run",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("control_room.write")),
    ],
)
async def intelligence_monte_carlo_run(
    body: MonteCarloRunRequest,
    user: dict = Depends(require_authenticated),
):
    _require_math_engines_enabled()
    return await monte_carlo_service.run_simulation(user, _payload(body))


@router.get(
    "/monte-carlo/{simulation_id}",
    dependencies=[Depends(require_permission("datasets.read"))],
)
@v1_router.get(
    "/monte-carlo/{simulation_id}",
    dependencies=[Depends(require_permission("datasets.read"))],
)
async def intelligence_monte_carlo_detail(
    simulation_id: str,
    user: dict = Depends(require_authenticated),
):
    return await monte_carlo_service.get_simulation(user, simulation_id)


@router.get("/monte-carlo", dependencies=[Depends(require_permission("datasets.read"))])
@v1_router.get(
    "/monte-carlo", dependencies=[Depends(require_permission("datasets.read"))]
)
async def intelligence_monte_carlo_list(
    source_type: Literal[
        "signal",
        "decision_option",
        "manual_fixture",
        "backtest_case",
        "wisdom_bit",
    ]
    | None = Query(default=None),
    source_id: str | None = Query(default=None, max_length=256),
    limit: int = Query(default=50, ge=1, le=250),
    user: dict = Depends(require_authenticated),
):
    return await monte_carlo_service.list_simulations(
        user,
        source_type=source_type,
        source_id=source_id,
        limit=limit,
    )


@router.post(
    "/run",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("control_room.write")),
    ],
)
@v1_router.post(
    "/run",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("control_room.write")),
    ],
)
async def intelligence_run(
    body: IntelligenceRunRequest | None = Body(default=None),
    mode: Literal["manual", "scheduled", "backtest", "smoke"] | None = Query(
        default=None
    ),
    user: dict = Depends(require_authenticated),
):
    payload = _payload(body)
    if mode is not None:
        payload["run_mode"] = mode
    result = await intelligence_engine.run_intelligence(user, payload)
    _invalidate_control_room_cache(user)
    return result


@router.post(
    "/runs",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("control_room.write")),
    ],
)
@v1_router.post(
    "/runs",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("control_room.write")),
    ],
)
async def intelligence_runs_create(
    body: IntelligenceRunRequest | None = Body(default=None),
    mode: Literal["manual", "scheduled", "backtest", "smoke"] | None = Query(
        default=None
    ),
    user: dict = Depends(require_authenticated),
):
    return await intelligence_run(body=body, mode=mode, user=user)


@router.get("/external/sources", dependencies=DATASETS_READ_DEPENDENCY)
@v1_router.get("/external/sources", dependencies=DATASETS_READ_DEPENDENCY)
async def intelligence_external_sources(
    user: dict = Depends(require_authenticated),
):
    return await intelligence_engine.list_sources(user)


@router.patch(
    "/external/sources/{source_id}",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("control_room.write")),
    ],
)
@v1_router.patch(
    "/external/sources/{source_id}",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("control_room.write")),
    ],
)
async def intelligence_update_external_source(
    source_id: str,
    body: ExternalSourcePatchRequest | None = Body(default=None),
    user: dict = Depends(require_authenticated),
):
    return await intelligence_engine.patch_source(user, source_id, _payload(body))


@router.post(
    "/external/run",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("control_room.write")),
    ],
)
@v1_router.post(
    "/external/run",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("control_room.write")),
    ],
)
async def intelligence_external_run(
    body: ExternalRunRequest | None = Body(default=None),
    user: dict = Depends(require_authenticated),
):
    result = await intelligence_engine.run_sources(user, _payload(body))
    _invalidate_control_room_cache(user)
    return result


@router.post(
    "/signals/{signal_id}/options/{option_id}/select",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("control_room.write")),
    ],
)
@v1_router.post(
    "/signals/{signal_id}/options/{option_id}/select",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("control_room.write")),
    ],
)
async def intelligence_select_option(
    signal_id: str,
    option_id: str,
    user: dict = Depends(require_authenticated),
):
    result = await intelligence_engine.select_option(user, signal_id, option_id)
    _invalidate_control_room_cache(user)
    return result


@router.post(
    "/signals/{signal_id}/outcome",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("control_room.write")),
    ],
)
@v1_router.post(
    "/signals/{signal_id}/outcome",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("control_room.write")),
    ],
)
async def intelligence_record_outcome(
    signal_id: str,
    body: OutcomeRequest,
    user: dict = Depends(require_authenticated),
):
    result = await intelligence_engine.record_outcome(user, signal_id, _payload(body))
    _invalidate_control_room_cache(user)
    return result
