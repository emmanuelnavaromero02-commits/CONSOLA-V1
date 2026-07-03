from __future__ import annotations

from app.services.security_context import build_security_context


def mcp_payload(tool: str, args: dict, user: dict | None = None) -> dict:
    payload = {"tool": tool, "args": args}
    if user is not None:
        payload["security_context"] = build_security_context(user)
    return payload
