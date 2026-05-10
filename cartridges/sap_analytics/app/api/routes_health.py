from fastapi import APIRouter

from app.core.sap_analytics_client import SAP AnalyticsClient

router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
def health() -> dict:
    return {"ok": True, "service": "sap_analytics"}


@router.get("/sap_analytics")
def health_sap_analytics() -> dict:
    client = SAP AnalyticsClient()
    info = client.test_connection()
    return {"ok": True, "service": "sap_analytics", **info}
