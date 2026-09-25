from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from app.services import mcp_registry
from app.dependencies import require_admin
from app.services import audit_service
from app.services.csrf import require_csrf
from app.services.tool_manifest import classify_tool


_SENSITIVE_ARG_FRAGMENTS = ("api_key", "authorization", "bearer", "client_secret", "password", "secret", "token")


def _scrub_args(value):
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if any(fragment in str(key).lower() for fragment in _SENSITIVE_ARG_FRAGMENTS):
                out[key] = "***"
            else:
                out[key] = _scrub_args(item)
        return out
    if isinstance(value, list):
        return [_scrub_args(item) for item in value]
    return value


router = APIRouter(
    prefix="/api/mcp",
    tags=["MCP UI"],
    dependencies=[Depends(require_admin)],
)


@router.get("/servers")
async def list_servers():
    return {"servers": await mcp_registry.list_servers()}


@router.post("/servers/register", dependencies=[Depends(require_csrf)])
async def register_server(body: dict, request: Request, user: dict = Depends(require_admin)):
    result = await mcp_registry.register(body)
    await audit_service.record_event(
        user_id=user.get("id"),
        email=user.get("email"),
        action="mcp.server.register",
        resource_type="mcp_server",
        resource_id=body.get("id") or body.get("name") or body.get("url"),
        status="success",
        metadata={"url": body.get("url"), "ip": request.client.host if request.client else None},
    )
    return result


@router.get("/servers/{server_id}/tools")
async def list_tools(server_id: str):
    return {"tools": await mcp_registry.list_tools(server_id)}


@router.post("/servers/{server_id}/invoke", dependencies=[Depends(require_csrf)])
async def invoke_tool(server_id: str, body: dict, user: dict = Depends(require_admin)):
    tool = body.get("tool")
    args = body.get("args", {})
    risk = classify_tool(tool or "")["risk_level"]
    try:
        result = await mcp_registry.invoke(server_id, tool, args, user=user)
    except HTTPException as exc:
        await audit_service.record_event(
            user_id=user.get("id"),
            email=user.get("email"),
            action="mcp.tool.invoke",
            resource_type="mcp_tool",
            resource_id=f"{server_id}__{tool}",
            status="error",
            metadata={"server": server_id, "tool": tool, "status_code": exc.status_code},
            tool_name=f"{server_id}__{tool}",
            tool_args=_scrub_args(args),
            tool_result_status="error",
            risk_level=risk,
        )
        raise
    status = "error" if isinstance(result, dict) and result.get("error") else "success"
    await audit_service.record_event(
        user_id=user.get("id"),
        email=user.get("email"),
        action="mcp.tool.invoke",
        resource_type="mcp_tool",
        resource_id=f"{server_id}__{tool}",
        status=status,
        metadata={"server": server_id, "tool": tool},
        tool_name=f"{server_id}__{tool}",
        tool_args=_scrub_args(args),
        tool_result_status=status,
        risk_level=risk,
    )
    return result


@router.post("/invoke", dependencies=[Depends(require_csrf)])
async def invoke_tool_generic(body: dict, user: dict = Depends(require_admin)):
    """Generic invoke: {server, tool, args}. Used by Studio UI for Pattern B actions."""
    server_id = body.get("server", "")
    tool = body.get("tool", "")
    args = body.get("args", {})
    risk = classify_tool(tool)["risk_level"]
    try:
        result = await mcp_registry.invoke(server_id, tool, args, user=user)
    except HTTPException as exc:
        await audit_service.record_event(
            user_id=user.get("id"),
            email=user.get("email"),
            action="mcp.tool.invoke",
            resource_type="mcp_tool",
            resource_id=f"{server_id}__{tool}",
            status="error",
            metadata={"server": server_id, "tool": tool, "status_code": exc.status_code},
            tool_name=f"{server_id}__{tool}",
            tool_args=_scrub_args(args),
            tool_result_status="error",
            risk_level=risk,
        )
        raise
    status = "error" if isinstance(result, dict) and result.get("error") else "success"
    await audit_service.record_event(
        user_id=user.get("id"),
        email=user.get("email"),
        action="mcp.tool.invoke",
        resource_type="mcp_tool",
        resource_id=f"{server_id}__{tool}",
        status=status,
        metadata={"server": server_id, "tool": tool},
        tool_name=f"{server_id}__{tool}",
        tool_args=_scrub_args(args),
        tool_result_status=status,
        risk_level=risk,
    )
    return {"result": result}


@router.post("/servers/health-check", dependencies=[Depends(require_csrf)])
async def health_check_servers():
    count = await mcp_registry.health_check_all()
    return {"checked": count}


@router.delete("/servers/{server_id}", dependencies=[Depends(require_csrf)])
async def deregister_server(server_id: str, user: dict = Depends(require_admin)):
    await mcp_registry.deregister(server_id)
    await audit_service.record_event(
        user_id=user.get("id"),
        email=user.get("email"),
        action="mcp.server.deregister",
        resource_type="mcp_server",
        resource_id=server_id,
        status="success",
        metadata={},
    )
    return {"ok": True}
