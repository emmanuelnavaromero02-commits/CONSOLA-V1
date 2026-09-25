from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.services import mcp_registry
from app.services.auth import verify_internal_api_key


router = APIRouter(
    prefix="/internal/mcp",
    tags=["MCP Registry"],
    dependencies=[Depends(verify_internal_api_key)],
)


_READ_SERVICES = {"console", "workspace", "airflow", "refinement", "mcp-infra", "agent_runner"}
_MUTATE_SERVICES = {"console", "mcp-infra"}
_INVOKE_SERVICES = {"console", "workspace", "airflow", "agent_runner"}
_SECURITY_SOURCE_BY_SERVICE = {
    "console": {"console", "agent_runner"},
    "workspace": {"workspace"},
    "airflow": {"airflow", "agent_runner"},
    "refinement": {"refinement"},
    "mcp-infra": {"mcp-infra"},
}
_GLOBAL_ADMIN_ROLES = {"owner", "super_admin", "admin"}


def _require_internal_service(service: str, allowed: set[str]) -> None:
    if service not in allowed:
        raise HTTPException(status_code=403, detail="internal service is not allowed for this MCP operation")


def _trusted_context(body: dict, internal_service: str) -> dict:
    ctx = body.get("security_context") if isinstance(body, dict) else None
    if not isinstance(ctx, dict) or not ctx.get("trusted"):
        raise HTTPException(status_code=403, detail="trusted security_context required")
    allowed_sources = _SECURITY_SOURCE_BY_SERVICE.get(internal_service, {internal_service})
    if str(ctx.get("source") or "") not in allowed_sources:
        raise HTTPException(status_code=403, detail="security_context source mismatch")
    role = str(ctx.get("role") or "").lower()
    if internal_service in {"workspace", "airflow"} and role in _GLOBAL_ADMIN_ROLES:
        if not (ctx.get("tenant_id") and ctx.get("workspace_id")):
            raise HTTPException(status_code=403, detail="unscoped admin context not allowed")
    return ctx


@router.get("/servers")
async def list_servers(internal_service: str = Depends(verify_internal_api_key)):
    _require_internal_service(internal_service, _READ_SERVICES)
    return {"servers": await mcp_registry.list_servers()}


class RegisterServerBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")
    name: str = Field(min_length=1, max_length=200)
    url: str = Field(min_length=1, max_length=2048)
    category: str = Field(default="other", max_length=64)
    description: str = Field(default="", max_length=2000)


@router.post("/servers/register")
async def register_server(body: dict, internal_service: str = Depends(verify_internal_api_key)):
    _require_internal_service(internal_service, _MUTATE_SERVICES)
    try:
        server = RegisterServerBody.model_validate(body).model_dump()
    except ValidationError:
        raise HTTPException(status_code=422, detail="invalid request body") from None
    result = await mcp_registry.register(server)
    return result


@router.get("/servers/{server_id}/tools")
async def list_tools(server_id: str, internal_service: str = Depends(verify_internal_api_key)):
    _require_internal_service(internal_service, _READ_SERVICES)
    return {"tools": await mcp_registry.list_tools(server_id)}


@router.post("/servers/{server_id}/invoke")
async def invoke_tool(server_id: str, body: dict, internal_service: str = Depends(verify_internal_api_key)):
    _require_internal_service(internal_service, _INVOKE_SERVICES)
    return await mcp_registry.invoke(
        server_id,
        body.get("tool"),
        body.get("args", {}),
        security_context=_trusted_context(body, internal_service),
    )


@router.post("/invoke")
async def invoke_tool_generic(body: dict, internal_service: str = Depends(verify_internal_api_key)):
    """Generic invoke: {server, tool, args}. Used by Studio UI for Pattern B actions."""
    _require_internal_service(internal_service, _INVOKE_SERVICES)
    server_id = body.get("server", "")
    tool = body.get("tool", "")
    args = body.get("args", {})
    result = await mcp_registry.invoke(server_id, tool, args, security_context=_trusted_context(body, internal_service))
    return {"result": result}


@router.post("/servers/health-check")
async def health_check_servers(internal_service: str = Depends(verify_internal_api_key)):
    _require_internal_service(internal_service, _MUTATE_SERVICES)
    count = await mcp_registry.health_check_all()
    return {"checked": count}


@router.delete("/servers/{server_id}")
async def deregister_server(server_id: str, internal_service: str = Depends(verify_internal_api_key)):
    _require_internal_service(internal_service, _MUTATE_SERVICES)
    await mcp_registry.deregister(server_id)
    return {"ok": True}
