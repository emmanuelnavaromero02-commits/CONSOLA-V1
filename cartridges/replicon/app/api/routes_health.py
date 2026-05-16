from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.core.replicon_client import RepliconClient

router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
def health(request: Request):
    # v1.43.2 (Codex P1-5): /health must mirror real startup state.
    # Returning 200 when a critical step failed silently is the bug
    # that put broken cartridges into rotation.
    state = request.app.state
    ok = getattr(state, "startup_ok", False)
    errors = list(getattr(state, "startup_errors", []) or [])
    body = {"ok": ok, "service": "replicon", "startup_errors": errors}
    return JSONResponse(body, status_code=200 if ok else 503)


@router.get("/replicon")
def health_replicon() -> dict:
    client = RepliconClient()
    info = client.test_connection()
    return {"ok": True, "service": "replicon", **info}
