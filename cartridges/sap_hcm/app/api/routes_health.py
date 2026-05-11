from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import verify_api_key
from app.core.sap_client import SapHcmClient

router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
def health() -> dict:
    return {"ok": True, "service": "sap_hcm"}


@router.get("/sap_hcm", dependencies=[Depends(verify_api_key)])
def health_sap_hcm() -> dict:
    info = SapHcmClient().test_connection()
    return {"service": "sap_hcm", **info}
