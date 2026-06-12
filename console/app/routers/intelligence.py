from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.dependencies import require_authenticated
from app.services import intelligence_engine
from app.services.intelligence.readiness import intelligence_readiness
from app.services.csrf import require_csrf
from app.services.permissions import require_permission


router = APIRouter(prefix="/api/intelligence", tags=["Intelligence"])
v1_router = APIRouter(prefix="/api/v1/intelligence", tags=["Intelligence"])


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IntelligenceRunRequest(_StrictModel):
    cartridge_id: str | None = Field(default=None, pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
    metrics: list[str] | None = None
    include_external: bool = False
    horizon_days: int | list[int] | None = None
    dry_run: bool = False

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
    def _validate_horizon_days(cls, value: int | list[int] | None) -> int | list[int] | None:
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


@router.get("/signals/{signal_id}", dependencies=[Depends(require_permission("datasets.read"))])
@v1_router.get("/signals/{signal_id}", dependencies=[Depends(require_permission("datasets.read"))])
async def intelligence_signal_detail(
    signal_id: str,
    user: dict = Depends(require_authenticated),
):
    return await intelligence_engine.get_signal(user, signal_id)


@router.get("/readiness", dependencies=[Depends(require_permission("datasets.read"))])
@v1_router.get("/readiness", dependencies=[Depends(require_permission("datasets.read"))])
async def intelligence_readiness_endpoint(
    require_data: bool = Query(default=True),
    user: dict = Depends(require_authenticated),
):
    return await intelligence_readiness(user, require_data=require_data)


@router.post(
    "/run",
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
)
@v1_router.post(
    "/run",
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
)
async def intelligence_run(
    body: IntelligenceRunRequest | None = Body(default=None),
    user: dict = Depends(require_authenticated),
):
    result = await intelligence_engine.run_intelligence(user, _payload(body))
    _invalidate_control_room_cache(user)
    return result


@router.get("/external/sources", dependencies=[Depends(require_permission("datasets.read"))])
@v1_router.get("/external/sources", dependencies=[Depends(require_permission("datasets.read"))])
async def intelligence_external_sources(
    user: dict = Depends(require_authenticated),
):
    return await intelligence_engine.list_sources(user)


@router.patch(
    "/external/sources/{source_id}",
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
)
@v1_router.patch(
    "/external/sources/{source_id}",
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
)
async def intelligence_update_external_source(
    source_id: str,
    body: ExternalSourcePatchRequest | None = Body(default=None),
    user: dict = Depends(require_authenticated),
):
    return await intelligence_engine.patch_source(user, source_id, _payload(body))


@router.post(
    "/external/run",
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
)
@v1_router.post(
    "/external/run",
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
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
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
)
@v1_router.post(
    "/signals/{signal_id}/options/{option_id}/select",
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
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
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
)
@v1_router.post(
    "/signals/{signal_id}/outcome",
    dependencies=[Depends(require_csrf), Depends(require_permission("control_room.write"))],
)
async def intelligence_record_outcome(
    signal_id: str,
    body: OutcomeRequest,
    user: dict = Depends(require_authenticated),
):
    result = await intelligence_engine.record_outcome(user, signal_id, _payload(body))
    _invalidate_control_room_cache(user)
    return result
