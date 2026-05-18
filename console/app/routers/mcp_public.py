from __future__ import annotations

from fastapi import APIRouter, Depends, Request

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


# Router para el navegador. Autenticación por sesión + RBAC.
# Para llamadas server-to-server usar /internal/mcp/* (mcp.py).
#
# Sprint v1.34 (audit B2 P0): every route here proxies to mcp-infra
# (and other registered MCP servers) using console's own
# INTERNAL_API_KEY. A non-admin authenticated user reaching ``/invoke``
# could therefore execute privileged tools (``postgres_execute_query``,
# ``airflow_trigger_dag``, ``airflow_set_variable``, etc.) under the
# console service identity — a full privilege-escalation primitive.
# Locking the router to ``require_admin`` removes that primitive; the
# non-admin Studio/Monitor flows already go through ``/studio_ops/*``
# and ``/monitoring/*`` which perform their own per-tool RBAC checks
# and never proxy with the internal key.
#
# Mutating browser calls are CSRF-protected. Studio and viewer/pipeline attach
# the same double-submit token used by the rest of the :8000 console.
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
    result = await mcp_registry.invoke(server_id, tool, args)
    risk = classify_tool(tool or "")["risk_level"]
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
    result = await mcp_registry.invoke(server_id, tool, args)
    risk = classify_tool(tool)["risk_level"]
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
