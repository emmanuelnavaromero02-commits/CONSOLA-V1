from fastapi import APIRouter

from app.core.sap_fi_co_client import SAP FI/CO FinanzasClient

router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
def health() -> dict:
    return {"ok": True, "service": "sap_fi_co"}


@router.get("/sap_fi_co")
def health_sap_fi_co() -> dict:
    client = SAP FI/CO FinanzasClient()
    info = client.test_connection()
    return {"ok": True, "service": "sap_fi_co", **info}
