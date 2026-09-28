from __future__ import annotations

import asyncio
import time
from typing import Any

from pydantic import BaseModel, ConfigDict

ORCHESTRATOR_CACHE_TTL_SECONDS = 30.0
ORCHESTRATOR_DESCRIBE_TIMEOUT_SECONDS = 4.0

_cache: dict[str, tuple[float, dict[str, Any]]] = {}


class SyncOrchestratorStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    airflow_available: bool = False
    scheduler_healthy: bool | None = None
    dag_paused: bool | None = None


def reset_orchestrator_status_cache() -> None:
    _cache.clear()


def sync_extract_all_dag_id(cartridge: str, overrides: dict[str, str]) -> str:
    normalized = str(cartridge or "").replace("-", "_")
    return overrides.get(cartridge) or f"{normalized}_extract_all"


def _status_from_describe(result: Any) -> SyncOrchestratorStatus:
    if not isinstance(result, dict) or result.get("error"):
        return SyncOrchestratorStatus()
    scheduler_healthy = result.get("scheduler_healthy")
    return SyncOrchestratorStatus(
        airflow_available=True,
        scheduler_healthy=scheduler_healthy
        if isinstance(scheduler_healthy, bool)
        else None,
        dag_paused=bool(result.get("is_paused")) if result.get("found") else None,
    )


async def sync_orchestrator_status(
    *,
    cartridge: str,
    dag_id: str,
    invoke: Any,
    user: dict[str, Any] | None,
    ttl_seconds: float = ORCHESTRATOR_CACHE_TTL_SECONDS,
    timeout_seconds: float = ORCHESTRATOR_DESCRIBE_TIMEOUT_SECONDS,
    now: Any = time.monotonic,
) -> dict[str, Any]:
    cached = _cache.get(cartridge)
    current = now()
    if cached and cached[0] > current:
        return dict(cached[1])
    try:
        result = await asyncio.wait_for(
            invoke("infra", "airflow_describe_dag", {"dag_id": dag_id}, user=user),
            timeout=timeout_seconds,
        )
    except Exception:
        result = None
    payload = _status_from_describe(result).model_dump()
    _cache[cartridge] = (current + ttl_seconds, payload)
    return dict(payload)
