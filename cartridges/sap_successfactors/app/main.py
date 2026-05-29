from __future__ import annotations

import inspect
import logging
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.deps import verify_api_key
from app.api.routes_health import router as health_router
from app.api.routes_console import router as console_router
from app.api.routes_skills import router as skills_router
from app.core import job_runner
from app.mcp_server import load_custom_tools, mcp
from app.security import InternalApiKeyASGIGuard, get_internal_api_key
from app.services import catalog_service

logger = logging.getLogger(__name__)


# ── FastMCP Streamable HTTP (JSON-RPC 2.0) at /mcp/rpc ───────────────────────

_mcp_app = mcp.http_app(path="/")


# ── Lifespan: schema migration + job runner init ──────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # v1.43.2 (Codex P1-5): track per-step startup state — see
    # cartridges/replicon/app/main.py for the rationale.
    app.state.startup_ok = False
    app.state.startup_errors = []

    get_internal_api_key()

    try:
        await job_runner.ensure_schema()
        await job_runner.cleanup_stale()
    except Exception as e:
        app.state.startup_errors.append(f"job_runner: {type(e).__name__}: {e}")

    try:
        # Register cartridge header + entities so Studio's dropdown lists
        # this source even if no client has hit /entities yet.
        catalog_service._seed_if_empty()
    except Exception as e:
        app.state.startup_errors.append(f"catalog_seed: {type(e).__name__}: {e}")

    if not app.state.startup_errors:
        app.state.startup_ok = True

    async with _mcp_app.router.lifespan_context(app):
        yield


app = FastAPI(title="SAP SuccessFactors Cartridge", lifespan=lifespan)


def _internal_error_request_id(request: Request | None = None) -> str:
    candidate = getattr(getattr(request, "state", None), "request_id", None)
    try:
        return str(uuid.UUID(str(candidate)))
    except Exception:
        return str(uuid.uuid4())


def _log_internal_error(exc: Exception, message: str, request: Request | None = None) -> str:
    request_id = _internal_error_request_id(request)
    logger.exception(
        "%s request_id=%s",
        message,
        request_id,
        extra={"request_id": request_id, "exception_type": type(exc).__name__},
    )
    return request_id


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception):
    request_id = _log_internal_error(exc, "unhandled sap_successfactors exception", request)
    return JSONResponse(
        {"error": "Internal Error", "request_id": request_id},
        status_code=500,
    )


# v1.43.1 (Codex P0-1): X-Request-ID middleware.
from app.middleware.request_id import RequestIDMiddleware  # noqa: E402

app.add_middleware(RequestIDMiddleware)

app.include_router(health_router)
app.include_router(skills_router)
app.include_router(console_router)


# v1.44.3.3 Task C — yes/no liveness probe; see replicon/sap_hcm
# for the full rationale.
@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True, "service": "sap_successfactors"}



# v1.43.2 (LLM R1 hardening): /mcp/* must respect startup state. See
# cartridges/replicon/app/main.py for the rationale.

class _MCPStartupGuard:
    """ASGI wrapper that 503s when startup_ok=False for /mcp/* paths."""

    def __init__(self, inner, fastapi_app: FastAPI):
        self._inner = inner
        self._app = fastapi_app

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http" and not getattr(
            self._app.state, "startup_ok", False,
        ):
            import json as _json
            errors = list(getattr(self._app.state, "startup_errors", []) or [])
            body = _json.dumps({
                "error": "cartridge_not_ready",
                "startup_errors": errors,
            }).encode("utf-8")
            await send({
                "type": "http.response.start",
                "status": 503,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            })
            await send({"type": "http.response.body", "body": body})
            return
        await self._inner(scope, receive, send)


app.mount("/mcp/rpc", _MCPStartupGuard(InternalApiKeyASGIGuard(_mcp_app), app))


def _require_startup_ok(request: "Request") -> None:
    from fastapi import HTTPException
    if not getattr(request.app.state, "startup_ok", False):
        errors = list(getattr(request.app.state, "startup_errors", []) or [])
        raise HTTPException(
            status_code=503,
            detail={"error": "cartridge_not_ready", "startup_errors": errors},
        )


from fastapi import Request  # noqa: E402


# ── REST adapter — contract for the console MCP registry ─────────────────────

def _tool_schema(tool_fn) -> dict:
    sig = inspect.signature(tool_fn)
    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, param in sig.parameters.items():
        ann = param.annotation
        default = param.default
        ptype = "string"
        if ann is int:
            ptype = "integer"
        elif ann is float:
            ptype = "number"
        elif ann is bool:
            ptype = "boolean"
        prop: dict[str, Any] = {"type": ptype}
        if default is inspect.Parameter.empty:
            required.append(name)
        else:
            prop["default"] = default
        properties[name] = prop
    return {"type": "object", "properties": properties, "required": required}


@app.get("/mcp/tools", dependencies=[Depends(verify_api_key), Depends(_require_startup_ok)])
async def mcp_tools():
    tool_list = await mcp.list_tools()
    tools = []
    for tool in tool_list:
        mcp_tool = tool.to_mcp_tool() if hasattr(tool, "to_mcp_tool") else None
        input_schema = (
            mcp_tool.inputSchema if mcp_tool and hasattr(mcp_tool, "inputSchema")
            else _tool_schema(tool.fn) if hasattr(tool, "fn") and tool.fn
            else {"type": "object", "properties": {}}
        )
        tools.append({
            "name": tool.name,
            "description": (tool.description or "").strip(),
            "input_schema": input_schema,
        })
    return {"tools": tools}


@app.post("/mcp/invoke", dependencies=[Depends(verify_api_key), Depends(_require_startup_ok)])
async def mcp_invoke(body: dict, request: Request):
    import json as _json
    tool_name = body.get("tool", "")
    args = body.get("args", {})

    tool = await mcp.get_tool(tool_name)
    if tool is None:
        return JSONResponse({"error": f"Tool '{tool_name}' not found"}, status_code=404)

    try:
        result = await tool.run(args)

        content_items = None
        if hasattr(result, "content"):
            content_items = result.content
        elif isinstance(result, list):
            content_items = result
        elif isinstance(result, dict) and "content" in result:
            content_items = result["content"]

        if content_items is not None:
            texts = []
            for item in content_items:
                text = getattr(item, "text", None) or (item.get("text") if isinstance(item, dict) else None)
                if text is not None:
                    try:
                        texts.append(_json.loads(text))
                    except Exception:
                        texts.append(text)
            if texts:
                return {"result": texts[0] if len(texts) == 1 else texts}

        return {"result": result}
    except Exception as exc:
        request_id = _log_internal_error(exc, "sap_successfactors mcp invoke failed", request)
        return JSONResponse(
            {"error": "Internal Error", "request_id": request_id},
            status_code=500,
        )


@app.post("/mcp-reload", dependencies=[Depends(verify_api_key), Depends(_require_startup_ok)])
def mcp_reload():
    count = load_custom_tools()
    return JSONResponse({"reloaded": count, "status": "ok"})
