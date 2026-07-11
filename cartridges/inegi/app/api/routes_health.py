from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.api.deps import verify_api_key

router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
def health() -> dict:
    return {"ok": True, "service": "inegi"}


@router.get("/inegi", dependencies=[Depends(verify_api_key)])
def health_inegi() -> JSONResponse:
    try:
        from app.core.inegi_client import INEGIClient
        from app.services.config_loader import load_series_configs
        from app.services.preflight_service import validate_metadata

        evidence = validate_metadata(INEGIClient(), load_series_configs())
        return JSONResponse({"ok": True, "service": "inegi", "series": evidence})
    except Exception as exc:
        return JSONResponse(
            {"ok": False, "service": "inegi", "error": type(exc).__name__},
            status_code=503,
        )
