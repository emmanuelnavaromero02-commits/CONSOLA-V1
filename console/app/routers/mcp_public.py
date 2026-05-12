from __future__ import annotations

from fastapi import APIRouter, Depends

from app.services import mcp_registry
from app.dependencies import require_authenticated


# Router para el navegador. Autenticación por sesión + RBAC.
# Para llamadas server-to-server usar /internal/mcp/* (mcp.py).
router = APIRouter(
    prefix="/api/mcp",
    tags=["MCP UI"],
    dependencies=[Depends(require_authenticated)],
)


@router.get("/servers")
async def list_servers():
    return {"servers": await mcp_registry.list_servers()}


@router.post("/servers/register")
async def register_server(body: dict):
    result = await mcp_registry.register(body)
    return result


@router.get("/servers/{server_id}/tools")
async def list_tools(server_id: str):
    return {"tools": await mcp_registry.list_tools(server_id)}


@router.post("/servers/{server_id}/invoke")
async def invoke_tool(server_id: str, body: dict):
    return await mcp_registry.invoke(server_id, body.get("tool"), body.get("args", {}))


@router.post("/invoke")
async def invoke_tool_generic(body: dict):
    """Generic invoke: {server, tool, args}. Used by Studio UI for Pattern B actions."""
    server_id = body.get("server", "")
    tool = body.get("tool", "")
    args = body.get("args", {})
    result = await mcp_registry.invoke(server_id, tool, args)
    return {"result": result}


@router.post("/servers/health-check")
async def health_check_servers():
    count = await mcp_registry.health_check_all()
    return {"checked": count}


@router.delete("/servers/{server_id}")
async def deregister_server(server_id: str):
    await mcp_registry.deregister(server_id)
    return {"ok": True}
