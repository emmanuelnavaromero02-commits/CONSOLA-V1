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
from app.mcp_server import load_custom_tools, mcp


# ── FastMCP Streamable HTTP (JSON-RPC 2.0) at /mcp/rpc ───────────────────────

_mcp_app = mcp.http_app(path="/")


# ── Lifespan: schema migration + job runner init ──────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await job_runner.ensure_schema()
        await job_runner.cleanup_stale()
    except Exception:
        # DB unavailable — cartridge still serves /health, /skills/entities (yaml fallback)
        pass
    async with _mcp_app.router.lifespan_context(app):
        yield


app = FastAPI(title="SAP S/4HANA Cartridge", lifespan=lifespan)

app.include_router(health_router)
app.include_router(skills_router)
app.mount("/mcp/rpc", _mcp_app)


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


@app.get("/mcp/tools", dependencies=[Depends(verify_api_key)])
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


@app.post("/mcp/invoke", dependencies=[Depends(verify_api_key)])
async def mcp_invoke(body: dict):
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
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.post("/mcp-reload", dependencies=[Depends(verify_api_key)])
def mcp_reload():
    count = load_custom_tools()
    return JSONResponse({"reloaded": count, "status": "ok"})
