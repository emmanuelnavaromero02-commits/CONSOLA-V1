from fastapi import APIRouter

from app.core.sap_checkin_client import SAP Check-In EmpleadosClient

router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
def health() -> dict:
    return {"ok": True, "service": "sap_checkin"}


@router.get("/sap_checkin")
def health_sap_checkin() -> dict:
    client = SAP Check-In EmpleadosClient()
    info = client.test_connection()
    return {"ok": True, "service": "sap_checkin", **info}
