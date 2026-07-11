from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.api.deps import verify_api_key

router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
def health() -> dict:
    return {"ok": True, "service": "sec_edgar"}


@router.get("/sec-edgar", dependencies=[Depends(verify_api_key)])
def health_sec_edgar() -> JSONResponse:
    try:
        from app.core.sec_client import SECClient
        from app.services.config_loader import load_company_configs
        from app.services.preflight_service import validate_metadata

        evidence = validate_metadata(SECClient(), load_company_configs())
        return JSONResponse({"ok": True, "service": "sec_edgar", "companies": evidence})
    except Exception as exc:
        return JSONResponse(
            {"ok": False, "service": "sec_edgar", "error": type(exc).__name__},
            status_code=503,
        )
