from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import require_authenticated, ROLE_ADMIN
from app.services import operations_service


async def _require_admin(user: dict = Depends(require_authenticated)) -> dict:
    role = (user or {}).get("role") or (user or {}).get("workspace_role")
    if role not in {ROLE_ADMIN, "owner", "super_admin"}:
        raise HTTPException(status_code=403, detail="admin role required")
    return user


router = APIRouter(
    prefix="/api/operations",
    tags=["Operations"],
)


@router.get("/migrations")
async def list_migrations(_: dict = Depends(_require_admin)):
    return {"migrations": await operations_service.list_migrations()}


@router.get("/health")
async def system_health(_: dict = Depends(_require_admin)):
    services = await operations_service.probe_services()
    up = sum(1 for s in services if s["status"] == "up")
    version = await operations_service.get_system_version()
    return {
        "version": version,
        "services": services,
        "summary": {"total": len(services), "up": up, "down": len(services) - up},
    }
