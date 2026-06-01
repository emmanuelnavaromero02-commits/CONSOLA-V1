from __future__ import annotations

import re
from contextvars import ContextVar, Token
from typing import Any


CARTRIDGE_ID = "sap_hcm"
SERVICE_SOURCE = "cartridge-sap_hcm"
_SAFE_SCOPE_SEGMENT = re.compile(r"[A-Za-z0-9_.:-]+")
_CURRENT_SECURITY_CONTEXT: ContextVar[dict[str, Any] | None] = ContextVar(
    "sap_hcm_security_context",
    default=None,
)


def set_security_context(ctx: dict[str, Any] | None) -> Token:
    if not isinstance(ctx, dict) or not ctx.get("trusted"):
        ctx = None
    return _CURRENT_SECURITY_CONTEXT.set(ctx)


def reset_security_context(token: Token) -> None:
    _CURRENT_SECURITY_CONTEXT.reset(token)


def get_security_context() -> dict[str, Any] | None:
    return _CURRENT_SECURITY_CONTEXT.get()


def _safe_segment(value: Any) -> str:
    text = str(value or "").strip()
    if not text or not _SAFE_SCOPE_SEGMENT.fullmatch(text):
        return ""
    return text


def scope_values(ctx: dict[str, Any] | None = None) -> tuple[str, str]:
    ctx = ctx if ctx is not None else get_security_context()
    if not isinstance(ctx, dict) or not ctx.get("trusted"):
        return "", ""
    tenant = _safe_segment(ctx.get("tenant_id"))
    workspace = _safe_segment(ctx.get("workspace_id"))
    if not (tenant and workspace):
        return "", ""
    return tenant, workspace


def scoped_prefix(ctx: dict[str, Any] | None = None) -> str:
    tenant, workspace = scope_values(ctx)
    if not (tenant and workspace):
        return ""
    return f"tenant_id={tenant}/workspace_id={workspace}/"


def refinement_security_context(ctx: dict[str, Any] | None = None) -> dict[str, Any]:
    ctx = ctx if ctx is not None else get_security_context()
    tenant, workspace = scope_values(ctx)
    if tenant and workspace:
        scope = f"tenant_id={tenant}/workspace_id={workspace}/"
        allowed_prefixes = [
            f"raw/{CARTRIDGE_ID}/{scope}",
            f"silver/{CARTRIDGE_ID}/{scope}",
            f"gold/{CARTRIDGE_ID}/{scope}",
        ]
    else:
        allowed_prefixes = [
            f"raw/{CARTRIDGE_ID}/",
            f"silver/{CARTRIDGE_ID}/",
            f"gold/{CARTRIDGE_ID}/",
        ]
    base: dict[str, Any] = {
        "trusted": True,
        "source": SERVICE_SOURCE,
        "role": "admin",
        "permissions": ["datasets.read", "datasets.write"],
        "allowed_buckets": ["lakehouse"],
        "allowed_cartridges": [CARTRIDGE_ID],
        "allowed_prefixes": allowed_prefixes,
    }
    if isinstance(ctx, dict):
        base["request_user_id"] = ctx.get("user_id") or ctx.get("request_user_id")
    if tenant and workspace:
        base["tenant_id"] = tenant
        base["workspace_id"] = workspace
    return base
