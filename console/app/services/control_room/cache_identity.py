from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from app.services.security_context import build_security_context


_ACCESS_REVISION_FIELDS = (
    "access_revision",
    "access_version",
    "authorization_revision",
    "authorization_version",
    "membership_revision",
    "permissions_revision",
    "permissions_version",
    "rbac_revision",
    "rbac_version",
)


@dataclass(frozen=True)
class AuthorizationCacheIdentity:
    tenant_id: str
    workspace_id: str
    global_role: str
    workspace_role: str
    user_id: str
    effective_permissions: tuple[str, ...]
    allowed_cartridges: tuple[str, ...]
    access_revisions: tuple[tuple[str, str], ...]


def _ordered_strings(values: Iterable[Any] | None) -> tuple[str, ...]:
    return tuple(
        sorted({str(value).strip() for value in (values or ()) if str(value).strip()})
    )


def _revision_value(value: Any) -> str:
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return str(value).strip()


def authorization_cache_identity(user: dict | None) -> AuthorizationCacheIdentity:
    source = user or {}
    context = build_security_context(user)
    permissions = source.get("_effective_permissions")
    if permissions is None:
        permissions = context.get("permissions")
    revisions = tuple(
        (field, _revision_value(source[field]))
        for field in _ACCESS_REVISION_FIELDS
        if source.get(field) is not None
    )
    return AuthorizationCacheIdentity(
        tenant_id=str(
            context.get("tenant_id")
            or source.get("active_tenant_id")
            or source.get("tenant_id")
            or ""
        ).strip(),
        workspace_id=str(
            context.get("workspace_id")
            or source.get("active_workspace_id")
            or source.get("workspace_id")
            or ""
        ).strip(),
        global_role=str(context.get("role") or source.get("role") or "").strip(),
        workspace_role=str(
            context.get("workspace_role") or source.get("workspace_role") or ""
        ).strip(),
        user_id=str(context.get("user_id") or source.get("id") or "").strip(),
        effective_permissions=_ordered_strings(permissions),
        allowed_cartridges=_ordered_strings(context.get("allowed_cartridges")),
        access_revisions=revisions,
    )
