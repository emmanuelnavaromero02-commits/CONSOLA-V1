from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.services.auth import verify_internal_api_key
from app.services import settings_service


# Router server-to-server. Llamadores: cartuchos (Replicon, SAP SF/HCM/S4HANA)
# que necesitan leer credenciales de system_settings sin tener sesión humana.
# La auth la valida verify_internal_api_key (x-api-key + x-internal-service).
router = APIRouter(
    prefix="/internal/settings",
    tags=["Settings (internal)"],
    dependencies=[Depends(verify_internal_api_key)],
)


@router.get("/{key}/reveal")
async def reveal_setting_internal(key: str):
    """Server-to-server: lee valor real de un setting (incluso secrets)."""
    item = await settings_service.get_setting(key, include_secret=True)
    if not item:
        raise HTTPException(status_code=404, detail="setting not found")
    return item
