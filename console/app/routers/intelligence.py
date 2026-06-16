from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Body, Depends, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.dependencies import require_authenticated
from app.services import intelligence_engine
from app.services.intelligence import backtesting as intelligence_backtesting
from app.services.intelligence import calibration_service
from app.services.intelligence import history as intelligence_history
from app.services.intelligence import monte_carlo_service
from app.services.intelligence.readiness import intelligence_readiness
from app.services.csrf import require_csrf
from app.services.permissions import require_permission


router = APIRouter(prefix="/api/intelligence", tags=["Intelligence"])
v1_router = APIRouter(prefix="/api/v1/intelligence", tags=["Intelligence"])
DATASETS_READ_DEPENDENCY = [Depends(require_permission("datasets.read"))]


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


class MonteCarloOptionRequest(_StrictModel):
    option_id: str = Field(min_length=1, max_length=120)
    label: str | None = Field(default=None, max_length=240)
    input_variables: dict[str, dict] | None = None
    assumptions: dict | None = None


class MonteCarloRunRequest(_StrictModel):
    source_type: Literal[
        "signal", "decision_option", "manual_fixture", "backtest_case"
    ]
    source_id: str = Field(min_length=1, max_length=256)
    horizon_days: int = Field(default=30, ge=1, le=365)
    iterations: int = Field(default=1000, ge=1, le=10_000)
    seed: int = Field(default=0, ge=0)
    model_version: str | None = Field(default=None, max_length=80)
    input_variables: dict[str, dict] = Field(default_factory=dict)
    assumptions: dict = Field(default_factory=dict)
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
    predicted_metric: str = Field(min_length=1, max_length=120)
    predicted_probability: float | None = Field(default=None, ge=0, le=1)
    predicted_value: float | None = None
    predicted_interval: dict | None = None
    actual_status: Literal["hit", "miss", "partial", "unknown"]
    actual_value: float | None = None
    observed_at: str | None = Field(default=None, max_length=80)
    horizon_days: int = Field(default=30, ge=1, le=3650)
    model_version: str | None = Field(default=None, max_length=120)
    calibration_group: str | None = Field(default=None, max_length=80)
    evidence_refs: list[dict] = Field(default_factory=list, max_length=20)


class CalibrationRecomputeRequest(_StrictModel):
    calibration_group: str = Field(min_length=1, max_length=80)
    model_version: str | None = Field(default=None, max_length=120)
    parent_calibration_group: str | None = Field(default=None, max_length=80)
    source_type: Literal[
        "monte_carlo_simulation",
        "decision_option",
        "prediction_outcome",
        "backtest_case",
        "manual_fixture",
    ] | None = None
    source_id: str | None = Field(default=None, max_length=256)
    limit: int = Field(default=5000, ge=1, le=10_000)


def _payload(model: BaseModel | None) -> dict:
    if model is None:
        return {}
    return model.model_dump(exclude_none=True)


def _invalidate_control_room_cache(user: dict) -> None:
    try:
        from app.routers.control_room import _control_room_cache_invalidate
    except Exception:
        return
    _control_room_cache_invalidate(user)


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
        "signal", "decision_option", "manual_fixture", "backtest_case"
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
