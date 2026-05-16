from __future__ import annotations

import inspect
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse

from app.api.deps import verify_api_key
from app.api.routes_health import router as health_router
from app.api.routes_skills import router as skills_router
from app.core import job_runner
from app.mcp_server import mcp, load_custom_tools
from app.security import InternalApiKeyASGIGuard, get_internal_api_key


# ── Lifespan: schema migration + job runner init ──────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # v1.43.2 (Codex P1-5): record per-step startup results so /health
    # can report a real readiness signal. Pre-v1.43.2, schema-migration
    # failures were silently swallowed and /health stayed ``ok: true``
    # — Kubernetes would route traffic at a broken cartridge.
    app.state.startup_ok = False
    app.state.startup_errors = []

    # get_internal_api_key() is intentionally NOT caught: a missing
    # INTERNAL_API_KEY is unrecoverable and must fail the process.
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


# ── FastMCP Streamable HTTP (JSON-RPC 2.0) at /mcp/rpc ───────────────────────
_mcp_app = mcp.http_app(path="/")

app = FastAPI(title="Replicon Cartridge", lifespan=lifespan)

# v1.43.1 (Codex P0-1): every response — including 401/403/404 from
# the InternalApiKeyASGIGuard and the FastAPI exception handlers —
# must carry an ``X-Request-ID`` header so operators can correlate a
# failed request with its server-side trace. Pure-ASGI middleware
# intercepts at the send() level so it survives every short-circuit
# auth path. Byte-identical to console/workspace/vault/refinement/mcp-infra
# (md5 7fe9a9120bfac6f028a9c7662afcccae).
from app.middleware.request_id import RequestIDMiddleware  # noqa: E402

app.add_middleware(RequestIDMiddleware)

app.include_router(health_router)
app.include_router(skills_router)


# v1.43.2 (LLM R1 hardening): /mcp/* must respect startup state. If
# lifespan recorded a failure (job_runner schema missing, etc.), the
# cartridge is in rotation only to /health (which already returns
# 503) — but a peer with the internal API key could still call
# /mcp/rpc | /mcp/tools | /mcp/invoke and trigger the very schema gap
# that flagged startup as broken. Fail-closed across the whole MCP
# surface keeps behaviour consistent with /health.

class _MCPStartupGuard:
    """ASGI wrapper that 503s when startup_ok=False for /mcp/* paths."""

    def __init__(self, inner, fastapi_app: FastAPI):
        self._inner = inner
        self._app = fastapi_app

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http" and not getattr(
            self._app.state, "startup_ok", False,
        ):
            errors = list(getattr(self._app.state, "startup_errors", []) or [])
            body = (
                b'{"error":"cartridge_not_ready","startup_errors":'
                + str(errors).replace("'", '"').encode("utf-8")
                + b"}"
            )
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
    """FastAPI dependency for the REST adapter endpoints (/mcp/tools,
    /mcp/invoke, /mcp-reload). Mirrors _MCPStartupGuard for the
    ASGI-mounted /mcp/rpc."""
    from fastapi import HTTPException
    if not getattr(request.app.state, "startup_ok", False):
        errors = list(getattr(request.app.state, "startup_errors", []) or [])
        raise HTTPException(
            status_code=503,
            detail={"error": "cartridge_not_ready", "startup_errors": errors},
        )


from fastapi import Request  # noqa: E402 — used by _require_startup_ok


# ── REST adapter — contract for the MODecissions console registry ─────────────
# GET  /mcp/tools  → {"tools": [...]}
# POST /mcp/invoke → {"tool": "name", "args": {...}} → result

def _tool_schema(tool_fn) -> dict:
    """Build input_schema from function signature annotations."""
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
async def mcp_invoke(body: dict):
    """Invoke a tool by name with args. Returns the tool result."""
    tool_name = body.get("tool", "")
    args = body.get("args", {})

    tool = await mcp.get_tool(tool_name)
    if tool is None:
        return JSONResponse({"error": f"Tool '{tool_name}' not found"}, status_code=404)

    try:
        import json as _json
        result = await tool.run(args)

        # FastMCP returns a ToolResult object with .content list of TextContent
        content_items = None
        if hasattr(result, "content"):          # ToolResult
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
        return JSONResponse({"error": str(exc)}, status_code=500)


# ── Custom tools reload ───────────────────────────────────────────────────────

@app.post("/mcp-reload", dependencies=[Depends(verify_api_key), Depends(_require_startup_ok)])
def mcp_reload():
    count = load_custom_tools()
    return JSONResponse({"reloaded": count, "status": "ok"})
