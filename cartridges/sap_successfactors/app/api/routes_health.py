from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import verify_api_key
from app.core.sap_client import SapSfClient

router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
def health() -> dict:
    """Public liveness probe (does not touch SAP)."""
    return {"ok": True, "service": "sap_successfactors"}


@router.get("/sap_successfactors", dependencies=[Depends(verify_api_key)])
def health_sap_successfactors() -> dict:
    """Configuration + connectivity check. Requires X-Internal-Api-Key."""
    client = SapSfClient()
    info = client.test_connection()
    return {"service": "sap_successfactors", **info}
