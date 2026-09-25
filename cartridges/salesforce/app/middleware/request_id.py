from __future__ import annotations

import contextvars
import logging
import re
import uuid

from starlette.datastructures import State


_log = logging.getLogger(__name__)
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)


def normalise_request_id(value: str | None) -> str:
    value = (value or "").strip()
    if _REQUEST_ID_RE.fullmatch(value):
        return value
    return str(uuid.uuid4())


class RequestIDMiddleware:

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        incoming = ""
        for name, value in scope.get("headers", []) or []:
            if name == b"x-request-id":
                incoming = value.decode("latin1", errors="replace")
                break
        rid = normalise_request_id(incoming)
        rid_bytes = rid.encode("ascii")

        token = request_id_var.set(rid)
        if "state" not in scope:
            scope["state"] = State()
        try:
            scope["state"].request_id = rid
        except Exception:
            pass

        response_started = False

        async def send_wrapper(message):
            nonlocal response_started
            mtype = message.get("type")
            if mtype in ("http.response.start", "websocket.accept"):
                if mtype == "http.response.start":
                    response_started = True
                headers = [
                    (k, v) for (k, v) in message.get("headers", []) or []
                    if k.lower() != b"x-request-id"
                ]
                headers.append((b"x-request-id", rid_bytes))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            _log.exception(
                "unhandled exception during request",
                extra={"request_id": rid},
            )
            if scope["type"] == "http" and not response_started:
                await send({
                    "type": "http.response.start",
                    "status": 500,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"x-request-id", rid_bytes),
                    ],
                })
                await send({
                    "type": "http.response.body",
                    "body": b'{"detail":"internal error"}',
                })
        finally:
            request_id_var.reset(token)
