from __future__ import annotations

import inspect
import json
import logging
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.deps import verify_api_key
from app.api.routes_health import router as health_router
from app.api.routes_skills import router as skills_router
from app.core import job_runner
from app.core.request_context import SecurityContextError, require_tenant_workspace_scope, reset_security_context, set_security_context
from app.mcp_server import mcp, load_custom_tools
from app.security import InternalApiKeyASGIGuard, get_internal_api_key

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.startup_ok = False
    app.state.startup_errors = []

    get_internal_api_key()

    try:
        await job_runner.ensure_schema()
        await job_runner.cleanup_stale()
    except Exception as e:
        app.state.startup_errors.append(f"job_runner: {type(e).__name__}: {e}")

    if not app.state.startup_errors:
        app.state.startup_ok = True

    async with _mcp_app.router.lifespan_context(app):
        yield


_mcp_app = mcp.http_app(path="/")

app = FastAPI(title="Replicon Cartridge", lifespan=lifespan)


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
    request_id = _log_internal_error(exc, "unhandled replicon exception", request)
    return JSONResponse(
        {"error": "Internal Error", "request_id": request_id},
        status_code=500,
    )


from app.middleware.request_id import RequestIDMiddleware  # noqa: E402

app.add_middleware(RequestIDMiddleware)

app.include_router(health_router)
app.include_router(skills_router)


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True, "service": "replicon"}


class _MCPStartupGuard:

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


class _MCPSecurityContextGuard:

    def __init__(self, inner):
        self._inner = inner

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            headers = {
                key.decode("latin1").lower(): value.decode("latin1")
                for key, value in scope.get("headers", [])
            }
            raw = headers.get("x-security-context")
            if not raw:
                response = JSONResponse({"error": "security_context_required"}, status_code=403)
                await response(scope, receive, send)
                return
            token = None
            try:
                ctx = json.loads(raw)
                token = set_security_context(ctx)
                require_tenant_workspace_scope()
            except (json.JSONDecodeError, SecurityContextError) as exc:
                if token is not None:
                    reset_security_context(token)
                response = JSONResponse(
                    {"error": "security_context_denied", "detail": str(exc)},
                    status_code=403,
                )
                await response(scope, receive, send)
                return
            try:
                await self._inner(scope, receive, send)
            finally:
                reset_security_context(token)
            return
        await self._inner(scope, receive, send)


app.mount("/mcp/rpc", _MCPStartupGuard(InternalApiKeyASGIGuard(_MCPSecurityContextGuard(_mcp_app)), app))


def _require_startup_ok(request: "Request") -> None:
    from fastapi import HTTPException
    if not getattr(request.app.state, "startup_ok", False):
        errors = list(getattr(request.app.state, "startup_errors", []) or [])
        raise HTTPException(
            status_code=503,
            detail={"error": "cartridge_not_ready", "startup_errors": errors},
        )


from fastapi import Request  # noqa: E402 — used by _require_startup_ok


def _tool_schema(tool_fn) -> dict:
    sig = inspect.signature(tool_fn)
    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, param in sig.parameters.items():
        ann = param.annotation
        default = param.default
        ptype = "string"
        if ann in (int,):
            ptype = "integer"
        elif ann in (float,):
            ptype = "number"
        elif ann in (bool,):
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
    """Return all registered MCP tools in the console registry format."""
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
            "name":         tool.name,
            "description":  (tool.description or "").strip(),
            "input_schema": input_schema,
        })
    return {"tools": tools}


@app.post("/mcp/invoke", dependencies=[Depends(verify_api_key), Depends(_require_startup_ok)])
async def mcp_invoke(body: dict, request: Request):
    """Invoke a tool by name with args. Returns the tool result."""
    tool_name = body.get("tool", "")
    args = body.get("args", {})
    expected_tenant_id = args.get("tenant_id") if isinstance(args, dict) else None
    expected_workspace_id = args.get("workspace_id") if isinstance(args, dict) else None

    tool = await mcp.get_tool(tool_name)
    if tool is None:
        return JSONResponse({"error": f"Tool '{tool_name}' not found"}, status_code=404)

    try:
        import json as _json
        token = set_security_context(
            body.get("security_context"),
            expected_tenant_id=expected_tenant_id,
            expected_workspace_id=expected_workspace_id,
        )
        try:
            result = await tool.run(args)
        finally:
            reset_security_context(token)

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
    except SecurityContextError as exc:
        return JSONResponse(
            {"error": "security_context_denied", "detail": str(exc)},
            status_code=403,
        )
    except Exception as exc:
        request_id = _log_internal_error(exc, "replicon mcp invoke failed", request)
        return JSONResponse(
            {"error": "Internal Error", "request_id": request_id},
            status_code=500,
        )


@app.post("/mcp-reload", dependencies=[Depends(verify_api_key), Depends(_require_startup_ok)])
def mcp_reload():
    count = load_custom_tools()
    return JSONResponse({"reloaded": count, "status": "ok"})
