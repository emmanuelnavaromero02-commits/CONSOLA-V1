from __future__ import annotations

import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.routes_health import router as health_router
from app.api.routes_skills import router as skills_router
from app.middleware.request_id import RequestIDMiddleware
from app.security import get_internal_api_key

logger = logging.getLogger(__name__)

app = FastAPI(title="Banxico Cartridge")
app.state.startup_ok = True
app.state.startup_errors = []

try:
    get_internal_api_key()
except Exception as exc:  # noqa: BLE001
    app.state.startup_ok = False
    app.state.startup_errors = [type(exc).__name__]

app.add_middleware(RequestIDMiddleware)
app.include_router(health_router)
app.include_router(skills_router)


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception):
    request_id = str(getattr(request.state, "request_id", "") or uuid.uuid4())
    logger.exception(
        "unhandled banxico exception request_id=%s",
        request_id,
        extra={"request_id": request_id, "exception_type": type(exc).__name__},
    )
    return JSONResponse({"error": "Internal Error", "request_id": request_id}, status_code=500)


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True, "service": "banxico"}
