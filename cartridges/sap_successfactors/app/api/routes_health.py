from fastapi import APIRouter

from app.core.sap_client import SapSfClient

router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
def health() -> dict:
    return {"ok": True, "service": "sap_successfactors"}


@router.get("/sap_successfactors")
def health_sap_successfactors() -> dict:
    client = SapSfClient()
    info = client.test_connection()
    return {"ok": True, "service": "sap_successfactors", **info}
