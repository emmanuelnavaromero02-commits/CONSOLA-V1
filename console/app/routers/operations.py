from __future__ import annotations

import json
import os
from datetime import datetime
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

from app.dependencies import ROLE_ADMIN
from app.security import get_internal_api_key
from app.services import auth, operations_service, scheduled_runtime
from app.services.auth import verify_internal_api_key
from app.services.permissions import canonical_role, require_permission
from app.services.security_context import build_security_context


async def _require_admin(
    user: dict = Depends(require_permission("operations.read")),
) -> dict:
    role = (user or {}).get("role") or (user or {}).get("workspace_role")
    if role not in {ROLE_ADMIN, "owner", "super_admin"}:
        raise HTTPException(status_code=403, detail="admin role required")
    return user


def _is_platform_admin(user: dict | None) -> bool:
    return canonical_role((user or {}).get("role")) in {
        "owner",
        "super_admin",
        ROLE_ADMIN,
    }


_OPERATIONAL_CARTRIDGES = {
    "banxico",
    "hubspot",
    "inegi",
    "replicon",
    "salesforce",
    "sec_edgar",
    "sap_hcm",
    "sap_s4hana",
    "sap_successfactors",
}
_VAULT_URL = os.environ.get("VAULT_URL", "http://vault:8300").rstrip("/")


def _vault_headers_for_user(user: dict | None) -> dict[str, str]:
    key = os.environ.get("INTERNAL_API_KEY_CONSOLE_TO_VAULT") or get_internal_api_key()
    return {
        "x-api-key": key,
        "x-internal-service": "console",
        "x-security-context": json.dumps(
            build_security_context(user), ensure_ascii=False
        ),
    }


async def _active_scoped_cartridges(user: dict | None) -> set[str]:
    active: set[str] = set()
    async with httpx.AsyncClient(
        headers=_vault_headers_for_user(user), timeout=5
    ) as client:
        for cartridge in sorted(_OPERATIONAL_CARTRIDGES):
            try:
                response = await client.get(
                    f"{_VAULT_URL}/connections/{quote(cartridge, safe='')}"
                )
            except Exception:
                continue
            if response.status_code in {404, 204} or response.status_code >= 400:
                continue
            try:
                payload = response.json()
            except ValueError:
                continue
            connections = (
                payload.get("connections") if isinstance(payload, dict) else []
            )
            if isinstance(connections, list) and any(
                isinstance(conn, dict) for conn in connections
            ):
                active.add(cartridge)
    return active


router = APIRouter(
    prefix="/api/operations",
    tags=["Operations"],
)


class _AgentRunnerWindow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    window_start: datetime
    window_end: datetime


@router.post("/internal/agent-runner/due")
async def scheduled_agent_fanout(
    body: _AgentRunnerWindow,
    internal_service: str = Depends(verify_internal_api_key),
):
    if internal_service != "airflow":
        raise HTTPException(403, "only airflow can discover scheduled agents")
    try:
        return await scheduled_runtime.find_due_agents(
            await auth.pool(),
            window_start=body.window_start,
            window_end=body.window_end,
        )
    except ValueError as exc:
        raise HTTPException(422, "invalid scheduler window") from exc


@router.get("/migrations")
async def list_migrations(_: dict = Depends(_require_admin)):
    return {"migrations": await operations_service.list_migrations()}


@router.get("/health")
async def system_health(user: dict = Depends(require_permission("operations.read"))):
    if not _is_platform_admin(user):
        version = await operations_service.get_system_version()
        return {
            "version": version,
            "services": [],
            "summary": {"total": 0, "up": 0, "down": 0},
            "scope": "workspace",
        }
    services = await operations_service.probe_services(
        await _active_scoped_cartridges(user)
    )
    up = sum(1 for s in services if s["status"] == "up")
    version = await operations_service.get_system_version()
    return {
        "version": version,
        "services": services,
        "summary": {"total": len(services), "up": up, "down": len(services) - up},
    }
