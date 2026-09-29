from __future__ import annotations

import json
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError

from app.services import mcp_registry
from app.dependencies import require_admin
from app.services import audit_service
from app.services import tool_policy
from app.services.csrf import require_csrf


_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


def _identifier(value: object, label: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER_RE.fullmatch(value):
        raise HTTPException(status_code=422, detail=f"invalid {label} identifier")
    return value


async def _record_invoke(
    user: dict,
    server_id: str,
    tool: str,
    args: object,
    *,
    status: str,
    risk: str,
    metadata: dict | None = None,
) -> None:
    await audit_service.record_event(
        user_id=user.get("id"),
        email=user.get("email"),
        action="mcp.tool.invoke",
        resource_type="mcp_tool",
        resource_id=f"{server_id}__{tool}",
        status=status,
        metadata={"server": server_id, "tool": tool, **(metadata or {})},
        tool_name=f"{server_id}__{tool}",
        tool_args=tool_policy.clip_args(tool_policy.scrub_args(args if isinstance(args, dict) else {})),
        tool_result_status=status,
        risk_level=risk,
    )


async def _policy_invoke(server_value: object, tool_value: object, args: object, user: dict):
    server_id = _identifier(server_value, "server")
    tool = _identifier(tool_value, "tool")
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise HTTPException(status_code=422, detail="tool args must be a JSON object")
    risk = tool_policy.classify(tool)["risk_level"]
    required = tool_policy.required_permission(risk)
    if not tool_policy.has_permission(user, risk):
        await _record_invoke(
            user, server_id, tool, args, status="denied", risk=risk,
            metadata={"required_permission": required},
        )
        raise HTTPException(status_code=403, detail=f"permission required: {required}")
    if len(json.dumps(args, default=str)) > tool_policy.MAX_TOOL_ARGS_BYTES:
        await _record_invoke(
            user, server_id, tool, args, status="rejected", risk=risk,
            metadata={"reason": "args_too_large"},
        )
        raise HTTPException(status_code=413, detail="tool args exceed size limit")
    schema = await mcp_registry.cached_tool_schema(server_id, tool)
    try:
        tool_policy.validate_tool_args(tool, args, schema, risk_level=risk)
    except tool_policy.ToolPolicyError as exc:
        await _record_invoke(
            user, server_id, tool, args, status="rejected", risk=risk,
            metadata={"reason": "tool_policy"},
        )
        raise HTTPException(status_code=422, detail=str(exc)) from None
    try:
        result = await mcp_registry.invoke(server_id, tool, args, user=user)
    except HTTPException as exc:
        await _record_invoke(
            user, server_id, tool, args, status="error", risk=risk,
            metadata={"status_code": exc.status_code},
        )
        raise
    status = "error" if isinstance(result, dict) and result.get("error") else "success"
    await _record_invoke(user, server_id, tool, args, status=status, risk=risk)
    return result


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
    from app.routers.mcp import RegisterServerBody

    try:
        body = RegisterServerBody.model_validate(body).model_dump()
    except ValidationError:
        raise HTTPException(status_code=422, detail="invalid request body") from None
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
    return await _policy_invoke(server_id, body.get("tool"), body.get("args", {}), user)


@router.post("/invoke", dependencies=[Depends(require_csrf)])
async def invoke_tool_generic(body: dict, user: dict = Depends(require_admin)):
    """Generic invoke: {server, tool, args}. Used by Studio UI for Pattern B actions."""
    result = await _policy_invoke(body.get("server", ""), body.get("tool", ""), body.get("args", {}), user)
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
