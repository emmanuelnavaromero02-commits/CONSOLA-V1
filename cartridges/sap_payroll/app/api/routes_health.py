from fastapi import APIRouter

from app.core.sap_client import SapPayrollClient

router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
def health() -> dict:
    return {"ok": True, "service": "sap_payroll"}


@router.get("/sap_payroll")
def health_sap_payroll() -> dict:
    client = SapPayrollClient()
    info = client.test_connection()
    return {"ok": True, "service": "sap_payroll", **info}
