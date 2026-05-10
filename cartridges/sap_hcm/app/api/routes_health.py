from fastapi import APIRouter

from app.core.sap_hcm_client import SAP HCM CoreClient

router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
def health() -> dict:
    return {"ok": True, "service": "sap_hcm"}


@router.get("/sap_hcm")
def health_sap_hcm() -> dict:
    client = SAP HCM CoreClient()
    info = client.test_connection()
    return {"ok": True, "service": "sap_hcm", **info}
