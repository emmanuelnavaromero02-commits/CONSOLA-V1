from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import verify_api_key
from app.core.sap_client import SapS4Client

router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
def health() -> dict:
    return {"ok": True, "service": "sap_s4hana"}


@router.get("/sap_s4hana", dependencies=[Depends(verify_api_key)])
def health_sap_s4hana() -> dict:
    info = SapS4Client().test_connection()
    return {"service": "sap_s4hana", **info}
