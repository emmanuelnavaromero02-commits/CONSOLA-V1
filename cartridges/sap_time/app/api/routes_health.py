from fastapi import APIRouter

from app.core.sap_time_client import SAP Time ManagementClient

router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
def health() -> dict:
    return {"ok": True, "service": "sap_time"}


@router.get("/sap_time")
def health_sap_time() -> dict:
    client = SAP Time ManagementClient()
    info = client.test_connection()
    return {"ok": True, "service": "sap_time", **info}
