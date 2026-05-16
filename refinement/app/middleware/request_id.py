"""Sprint v1.41.1 — Request correlation IDs.

Adds X-Request-ID header to every request/response. Generates one
if the client didn't provide it. Stored in request.state and exposed
to structured logs via contextvars (used by logging_config.JSONFormatter).
"""
from __future__ import annotations

import contextvars
import logging
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse


_log = logging.getLogger(__name__)


request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        rid = request.headers.get("x-request-id") or str(uuid.uuid4())
        token = request_id_var.set(rid)
        request.state.request_id = rid
        try:
            response = await call_next(request)
        except Exception:
            # Downstream blew up. We still owe the client an X-Request-ID
            # so they can quote it when reporting the failure, so build
            # the 500 ourselves with the header stamped and log a
            # correlated trace before returning.
            _log.exception(
                "unhandled exception during request", extra={"request_id": rid}
            )
            return JSONResponse(
                status_code=500,
                content={"detail": "internal error", "request_id": rid},
                headers={"X-Request-ID": rid},
            )
        finally:
            request_id_var.reset(token)
        response.headers["X-Request-ID"] = rid
        return response
