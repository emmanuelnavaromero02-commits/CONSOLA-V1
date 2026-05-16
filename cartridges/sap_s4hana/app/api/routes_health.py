from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.api.deps import verify_api_key
from app.core.sap_client import SapS4Client

router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
def health(request: Request):
    # v1.43.2 (Codex P1-5): mirror real startup state — see replicon.
    state = request.app.state
    ok = getattr(state, "startup_ok", False)
    errors = list(getattr(state, "startup_errors", []) or [])
    body = {"ok": ok, "service": "sap_s4hana", "startup_errors": errors}
    return JSONResponse(body, status_code=200 if ok else 503)


@router.get("/sap_s4hana", dependencies=[Depends(verify_api_key)])
def health_sap_s4hana() -> dict:
    info = SapS4Client().test_connection()
    return {"service": "sap_s4hana", **info}
