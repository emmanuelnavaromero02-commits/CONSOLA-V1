from fastapi import APIRouter

from app.core.sap_client import SapTimeClient

router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
def health() -> dict:
    return {"ok": True, "service": "sap_time"}


@router.get("/sap_time")
def health_sap_time() -> dict:
    client = SapTimeClient()
    info = client.test_connection()
    return {"ok": True, "service": "sap_time", **info}
