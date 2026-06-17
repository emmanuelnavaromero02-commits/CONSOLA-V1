"""Default-deny MCP gate for the legacy assistant chat surface."""
from __future__ import annotations

import os
from typing import Any

from app.services import mcp_registry, permissions, tool_manifest, tool_policy


DENIED_ERROR = "tool_denied"


def _configured_allowlist() -> set[str]:
    raw = os.environ.get("ASSISTANT_TOOL_ALLOWLIST", "")
    return {item.strip() for item in raw.split(",") if item.strip()}


def _tool_full_name(server_id: str, tool: str) -> str:
    return f"{server_id}__{tool}"


def _tenant_workspace_scope(user: dict | None) -> tuple[str, str]:
    if not user:
        return "", ""
    tenant_id = str(user.get("active_tenant_id") or user.get("tenant_id") or "").strip()
    workspace_id = str(user.get("active_workspace_id") or user.get("workspace_id") or "").strip()
    return tenant_id, workspace_id


def _is_platform_admin(user: dict | None) -> bool:
    return str((user or {}).get("role") or "").strip() in {"admin", "owner", "super_admin"}


def _has_required_scope(user: dict | None) -> bool:
    if _is_platform_admin(user):
        return True
    tenant_id, workspace_id = _tenant_workspace_scope(user)
    return bool(tenant_id and workspace_id)


def _allowed_read_tool(full_name: str, bare_name: str, meta: dict[str, Any]) -> bool:
    configured = _configured_allowlist()
    if configured and full_name not in configured and bare_name not in configured:
        return False
    if meta.get("risk_level") != "read" or meta.get("requires_approval"):
        return False
    return bare_name in tool_manifest.READ_ONLY_TOOLS or full_name in configured


def _deny(full_name: str, reason: str, *, risk_level: str = "unknown") -> dict[str, Any]:
    return {
        "error": DENIED_ERROR,
        "tool": full_name,
        "risk_level": risk_level,
        "message": reason,
    }


async def build_tools(user: dict | None = None) -> tuple[list[dict], dict[str, str], dict[str, dict[str, Any]]]:
    """Return Assistant-visible tools, tool_server_map, and an invocation catalog.

    Only explicit read-only tools from the static manifest are exposed. Unknown,
    write, destructive, approval-required, or permissionless tools stay invisible
    to the LLM and are denied again at invocation time.
    """
    if not permissions.has_permission(user, "copilot.use") or not _has_required_scope(user):
        return [], {}, {}

    servers = await mcp_registry.list_servers()
    tools: list[dict] = []
    tool_server_map: dict[str, str] = {}
    catalog: dict[str, dict[str, Any]] = {}

    for server in servers:
        if not server.get("healthy"):
            continue
        server_id = str(server.get("id") or "")
        if not server_id:
            continue
        for tool in server.get("tools") or []:
            bare_name = str(tool.get("name") or "")
            if not bare_name:
                continue
            full_name = _tool_full_name(server_id, bare_name)
            meta = tool_policy.classify(bare_name)
            if not _allowed_read_tool(full_name, bare_name, meta):
                continue
            input_schema = tool.get("input_schema") or tool.get("inputSchema") or {"type": "object", "properties": {}}
            catalog[full_name] = {
                "server_id": server_id,
                "server_name": server.get("name") or server_id,
                "tool": bare_name,
                "input_schema": input_schema,
                **meta,
            }
            tools.append({
                "name": full_name,
                "description": f"[safe read-only][{server.get('name') or server_id}] {tool.get('description', '')}",
                "input_schema": input_schema,
            })
            tool_server_map[full_name] = server_id

    return tools, tool_server_map, catalog


async def invoke(
    server: str,
    tool: str,
    args: dict | None,
    user: dict | None,
    catalog: dict[str, dict[str, Any]] | None,
) -> dict:
    full_name = _tool_full_name(server, tool)
    meta = tool_policy.classify(tool)

    if not permissions.has_permission(user, "copilot.use"):
        return _deny(full_name, "missing permission: copilot.use", risk_level=meta["risk_level"])
    if not _has_required_scope(user):
        return _deny(full_name, "active tenant/workspace scope is required", risk_level=meta["risk_level"])
    if not _allowed_read_tool(full_name, tool, meta):
        return _deny(full_name, "assistant may only auto-run allowlisted read-only tools", risk_level=meta["risk_level"])

    entry = (catalog or {}).get(full_name)
    if not entry:
        return _deny(full_name, "tool was not present in the assistant catalog", risk_level=meta["risk_level"])
    if entry.get("server_id") != server:
        return _deny(full_name, "tool catalog server mismatch", risk_level=meta["risk_level"])

    try:
        safe_args = tool_policy.validate_tool_args(
            tool,
            args or {},
            entry.get("input_schema"),
            risk_level="read",
        )
    except tool_policy.ToolPolicyError as exc:
        return _deny(full_name, str(exc), risk_level=meta["risk_level"])

    return await mcp_registry.invoke(server, tool, safe_args, user=user)
