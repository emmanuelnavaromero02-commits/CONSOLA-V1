from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import require_authenticated, ROLE_ADMIN
from app.services import settings_service


async def _require_admin(user: dict = Depends(require_authenticated)) -> dict:
    role = (user or {}).get("role") or (user or {}).get("workspace_role")
    if role != ROLE_ADMIN:
        raise HTTPException(status_code=403, detail="admin role required")
    return user


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


@router.post("/{key}/reveal")
async def reveal_setting(key: str, user: dict = Depends(_require_admin)):
    item = await settings_service.reveal_setting(key, user_id=user["id"], user_email=user.get("email"))
    if not item:
        raise HTTPException(status_code=404, detail="setting not found")
    return item


@router.put("/{key}")
async def update_setting(key: str, body: dict, user: dict = Depends(_require_admin)):
    if "value" not in body:
        raise HTTPException(status_code=400, detail="missing 'value' in body")
    try:
        return await settings_service.set_setting(key, body["value"], user_id=user["id"], user_email=user.get("email"))
    except KeyError:
        raise HTTPException(status_code=404, detail="setting not found")


@router.post("/{key}/rotate")
async def rotate_secret(key: str, user: dict = Depends(_require_admin)):
    try:
        return await settings_service.rotate_secret(key, user_id=user["id"], user_email=user.get("email"))
    except KeyError:
        raise HTTPException(status_code=404, detail="setting not found")
