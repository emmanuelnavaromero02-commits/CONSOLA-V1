from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.api.deps import verify_api_key
from app.core.sap_client import SapSfClient

router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
def health(request: Request):
    """v1.43.2 (Codex P1-5): mirror real startup state — see replicon."""
    state = request.app.state
    ok = getattr(state, "startup_ok", False)
    errors = list(getattr(state, "startup_errors", []) or [])
    body = {"ok": ok, "service": "sap_successfactors", "startup_errors": errors}
    return JSONResponse(body, status_code=200 if ok else 503)


@router.get("/sap_successfactors", dependencies=[Depends(verify_api_key)])
def health_sap_successfactors() -> dict:
    """Configuration + connectivity check. Requires X-Internal-Api-Key."""
    client = SapSfClient()
    info = client.test_connection()
    return {"service": "sap_successfactors", **info}
