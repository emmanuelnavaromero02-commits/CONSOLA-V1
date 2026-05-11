from fastapi import APIRouter

from app.core.sap_client import SapHcmClient

router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
def health() -> dict:
    return {"ok": True, "service": "sap_hcm"}


@router.get("/sap_hcm")
def health_sap_hcm() -> dict:
    client = SapHcmClient()
    info = client.test_connection()
    return {"ok": True, "service": "sap_hcm", **info}
