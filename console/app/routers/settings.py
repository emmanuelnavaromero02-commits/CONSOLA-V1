from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from app.dependencies import require_authenticated, ROLE_ADMIN
from app.services import settings_service
from app.services.csrf import require_csrf


async def _require_admin(user: dict = Depends(require_authenticated)) -> dict:
    role = (user or {}).get("role") or (user or {}).get("workspace_role")
    if role != ROLE_ADMIN:
        raise HTTPException(status_code=403, detail="admin role required")
    return user


def _forensic(request: Request) -> tuple[str | None, str | None]:
    """Sprint v1.41.0: extract ip + user_agent for audit_events. Settings
    mutations are admin-only and persisted forever — every call site must
    record where the action came from."""
    return (
        request.client.host if request.client else None,
        request.headers.get("user-agent"),
    )


router = APIRouter(
    prefix="/api/settings",
    tags=["Settings"],
)


@router.get("")
async def list_settings(category: str | None = None, _: dict = Depends(_require_admin)):
    return {"settings": await settings_service.list_settings(category=category, include_secrets=False)}


@router.get("/{key}")
async def get_setting(key: str, _: dict = Depends(_require_admin)):
    item = await settings_service.get_setting(key, include_secret=False)
    if not item:
        raise HTTPException(status_code=404, detail="setting not found")
    return item


@router.post("/{key}/reveal", dependencies=[Depends(require_csrf)])
async def reveal_setting(key: str, request: Request, user: dict = Depends(_require_admin)):
    ip, ua = _forensic(request)
    item = await settings_service.reveal_setting(
        key, user_id=user["id"], user_email=user.get("email"), ip=ip, user_agent=ua,
    )
    if not item:
        raise HTTPException(status_code=404, detail="setting not found")
    return item


@router.put("/{key}", dependencies=[Depends(require_csrf)])
async def update_setting(key: str, body: dict, request: Request, user: dict = Depends(_require_admin)):
    if "value" not in body:
        raise HTTPException(status_code=400, detail="missing 'value' in body")
    ip, ua = _forensic(request)
    try:
        return await settings_service.set_setting(
            key, body["value"], user_id=user["id"], user_email=user.get("email"),
            ip=ip, user_agent=ua,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="setting not found")


@router.post("/{key}/rotate", dependencies=[Depends(require_csrf)])
async def rotate_secret(key: str, request: Request, user: dict = Depends(_require_admin)):
    ip, ua = _forensic(request)
    try:
        return await settings_service.rotate_secret(
            key, user_id=user["id"], user_email=user.get("email"), ip=ip, user_agent=ua,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="setting not found")
