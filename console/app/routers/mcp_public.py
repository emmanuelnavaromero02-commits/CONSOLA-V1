from __future__ import annotations

from fastapi import APIRouter, Depends

from app.services import mcp_registry
from app.dependencies import require_admin


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
# DEUDA EXPLÍCITA (deferred to a dedicated CSRF sprint): the mutating
# routes here (POST /invoke, DELETE /servers/{id}, etc.) are not yet
# CSRF-protected. Adding ``require_csrf`` here would break the Studio
# and viewer/pipeline UIs that call ``/api/mcp/invoke`` via the plain
# ``apiFetch()`` helper (console/app/static/js/app.js) and 14+ direct
# ``fetch('/api/mcp/invoke', …)`` calls in
# console/app/static/js/studio/legacy.js — none of them currently send
# the X-CSRF-Token header. This belongs in the v1.35+ CSRF sweep that
# also clears ``KNOWN_CSRF_GAPS_FOR_LATER`` in tests/test_csrf_coverage.py.
# Until then ``require_admin`` is the chokepoint: the only cookie-bearing
# users who reach these routes are global admins.
router = APIRouter(
    prefix="/api/mcp",
    tags=["MCP UI"],
    dependencies=[Depends(require_admin)],
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
