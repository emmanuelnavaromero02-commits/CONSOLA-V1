from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.api.deps import verify_api_key

router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
def health() -> dict:
    return {"ok": True, "service": "banxico"}


@router.get("/banxico", dependencies=[Depends(verify_api_key)])
def health_banxico() -> JSONResponse:
    try:
        from app.core.banxico_client import BanxicoClient
        from app.services.config_loader import load_series_configs
        from app.services.preflight_service import validate_metadata

        evidence = validate_metadata(BanxicoClient(), load_series_configs())
        return JSONResponse({"ok": True, "service": "banxico", "series": evidence})
    except Exception as exc:
        return JSONResponse(
            {"ok": False, "service": "banxico", "error": type(exc).__name__},
            status_code=503,
        )
