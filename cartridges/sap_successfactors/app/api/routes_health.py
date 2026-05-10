from fastapi import APIRouter

from app.core.sap_successfactors_client import SAP SuccessFactorsClient

router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
def health() -> dict:
    return {"ok": True, "service": "sap_successfactors"}


@router.get("/sap_successfactors")
def health_sap_successfactors() -> dict:
    client = SAP SuccessFactorsClient()
    info = client.test_connection()
    return {"ok": True, "service": "sap_successfactors", **info}
