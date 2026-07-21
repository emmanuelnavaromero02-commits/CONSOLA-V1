from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import Awaitable, Callable, Iterable
from copy import deepcopy
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


CacheKey = tuple[str, AuthorizationCacheIdentity]
Loader = Callable[[], Awaitable[Any]]

READ_CACHE: dict[CacheKey, tuple[float, Any]] = {}
READ_CACHE_LOCKS: dict[CacheKey, asyncio.Lock] = {}


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


def cache_ttl() -> float:
    raw = os.environ.get("OMEGA_CONTROL_ROOM_CACHE_TTL_SECONDS", "15")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 15.0
    return max(0.0, min(value, 300.0))


def cache_key(namespace: str, user: dict | None) -> CacheKey:
    return namespace, authorization_cache_identity(user)


def cache_get(namespace: str, user: dict | None) -> Any | None:
    if cache_ttl() <= 0:
        return None
    key = cache_key(namespace, user)
    cached = READ_CACHE.get(key)
    if not cached:
        return None
    expires_at, value = cached
    if expires_at <= time.monotonic():
        READ_CACHE.pop(key, None)
        return None
    return deepcopy(value)


def cache_set(namespace: str, user: dict | None, value: Any) -> Any:
    ttl = cache_ttl()
    if ttl > 0:
        READ_CACHE[cache_key(namespace, user)] = (
            time.monotonic() + ttl,
            deepcopy(value),
        )
    return value


async def cache_get_or_set(
    namespace: str,
    user: dict | None,
    loader: Loader,
) -> Any:
    cached = cache_get(namespace, user)
    if cached is not None:
        return cached
    if cache_ttl() <= 0:
        return await loader()
    key = cache_key(namespace, user)
    lock = READ_CACHE_LOCKS.setdefault(key, asyncio.Lock())
    async with lock:
        cached = cache_get(namespace, user)
        if cached is not None:
            return cached
        return cache_set(namespace, user, await loader())


def cache_invalidate(user: dict | None) -> None:
    identity = authorization_cache_identity(user)
    for key in [key for key in READ_CACHE if key[1] == identity]:
        READ_CACHE.pop(key, None)
