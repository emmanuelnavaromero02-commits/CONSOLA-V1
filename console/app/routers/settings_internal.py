from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException

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
async def reveal_setting_internal(
    key: str,
    x_internal_service: str | None = Header(None),
):
    """Server-to-server: lee valor real de un setting (incluso secrets)."""
    allowed_prefixes = {
        "cartridge-replicon": ("replicon_",),
        "replicon": ("replicon_",),
        "cartridge-hubspot": ("hubspot_",),
        "hubspot": ("hubspot_",),
        "cartridge-salesforce": ("salesforce_",),
        "salesforce": ("salesforce_",),
        "cartridge-sap_hcm": ("sap_hcm_",),
        "cartridge-sap_s4hana": ("sap_s4hana_",),
        "cartridge-sap_successfactors": ("sap_successfactors_",),
        "cartridge-sap_b1": ("sap_b1_",),
    }
    prefixes = allowed_prefixes.get(x_internal_service or "")
    if not prefixes or not key.startswith(prefixes):
        raise HTTPException(status_code=403, detail="setting not allowed for service")
    item = await settings_service.get_setting(key, include_secret=True)
    if not item:
        raise HTTPException(status_code=404, detail="setting not found")
    return item
