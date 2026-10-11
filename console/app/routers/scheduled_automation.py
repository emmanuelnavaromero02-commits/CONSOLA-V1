from __future__ import annotations

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, ConfigDict, Field

from app.services import auth, scheduled_automation
from app.services.auth import verify_internal_api_key


router = APIRouter(tags=["Scheduled automation (internal)"])

_UUID_PATTERN = r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
_RUN_PATTERN = r"^[A-Za-z0-9_.:+~-]{1,250}$"


class _RefreshScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(pattern=_UUID_PATTERN)
    workspace_id: str = Field(pattern=_UUID_PATTERN)
    refresh_run_id: str = Field(pattern=_RUN_PATTERN)


class _RefreshMonitorInvocation(_RefreshScope):
    agent_id: str = Field(pattern=_UUID_PATTERN)
    airflow_dag_run_id: str | None = Field(default=None, pattern=_RUN_PATTERN)


@router.post(
    "/internal/automation/successfactors/daily-targets",
    dependencies=[Depends(verify_internal_api_key)],
)
async def successfactors_daily_targets(
    internal_service: str = Depends(verify_internal_api_key),
):
    scheduled_automation.require_airflow(internal_service)
    return await scheduled_automation.successfactors_targets(await auth.pool())


@router.post(
    "/internal/automation/refresh-monitors/due",
    dependencies=[Depends(verify_internal_api_key)],
)
async def refresh_monitors_due(
    body: _RefreshScope,
    internal_service: str = Depends(verify_internal_api_key),
    runner_token: str | None = Header(default=None, alias="X-Agent-Runner-Token"),
):
    scheduled_automation.require_airflow(internal_service)
    scheduled_automation.require_runner_token(runner_token)
    return await scheduled_automation.refresh_monitors_due(
        await auth.pool(),
        tenant_id=body.tenant_id.lower(),
        workspace_id=body.workspace_id.lower(),
        refresh_run_id=body.refresh_run_id,
    )


@router.post(
    "/internal/automation/refresh-monitors/invoke",
    dependencies=[Depends(verify_internal_api_key)],
)
async def refresh_monitor_invoke(
    body: _RefreshMonitorInvocation,
    internal_service: str = Depends(verify_internal_api_key),
    runner_token: str | None = Header(default=None, alias="X-Agent-Runner-Token"),
):
    scheduled_automation.require_airflow(internal_service)
    scheduled_automation.require_runner_token(runner_token)
    return await scheduled_automation.invoke_refresh_monitor(
        await auth.pool(),
        agent_id=body.agent_id.lower(),
        tenant_id=body.tenant_id.lower(),
        workspace_id=body.workspace_id.lower(),
        refresh_run_id=body.refresh_run_id,
        airflow_dag_run_id=body.airflow_dag_run_id,
    )
