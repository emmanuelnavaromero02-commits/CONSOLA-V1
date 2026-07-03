from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any, AsyncContextManager
from urllib.parse import quote

from fastapi import HTTPException


RequireUserAccess = Callable[[dict | None], None]
RequireCartridgeAccess = Callable[[dict | None, str], None]
ContextFactory = Callable[[dict | None], dict[str, Any]]
WorkspaceMemberships = Callable[[int], Awaitable[list[dict[str, Any]]]]
DbPoolFactory = Callable[[], Awaitable[Any]]
WorkspaceScopeResolver = Callable[[dict | None], Awaitable[tuple[str, str]]]
ScopedDbForUser = Callable[[Any, dict], AsyncContextManager[tuple[Any, Any, Any]]]
ScopedUserFactory = Callable[[dict | None, str, str], dict | None]
VisibleCartridges = Callable[[dict | None], set[str] | None]
VaultHeaders = Callable[[dict], dict[str, str]]
HttpClientFactory = Callable[..., AsyncContextManager[Any]]
LoggerDebug = Callable[..., None]


def normalize_candidate_cartridges(candidates: set[str] | None) -> set[str]:
    return {
        str(cartridge).strip()
        for cartridge in (candidates or set())
        if str(cartridge).strip()
    }


async def active_scoped_connection_cartridges(
    user: dict | None,
    candidate_cartridges: set[str] | None,
    *,
    scope_resolver: WorkspaceScopeResolver,
    scoped_user_factory: ScopedUserFactory,
    vault_url: str,
    vault_headers_for_user: VaultHeaders,
    http_client_factory: HttpClientFactory,
    logger_debug: LoggerDebug | None = None,
) -> set[str]:
    candidates = normalize_candidate_cartridges(candidate_cartridges)
    if not candidates:
        return set()
    tenant_id, workspace_id = await scope_resolver(user)
    scoped_user = scoped_user_factory(user, tenant_id, workspace_id)
    active: set[str] = set()
    for cartridge in sorted(candidates):
        try:
            async with http_client_factory(
                headers=vault_headers_for_user(scoped_user or {}), timeout=5
            ) as client:
                response = await client.get(
                    f"{vault_url}/connections/{quote(cartridge, safe='')}"
                )
        except Exception:
            if logger_debug:
                logger_debug(
                    "Failed to load scoped Vault connections for %s",
                    cartridge,
                    exc_info=True,
                )
            continue
        if response.status_code in {404, 204} or response.status_code >= 400:
            continue
        try:
            payload = response.json()
        except ValueError:
            continue
        connections = payload.get("connections") if isinstance(payload, dict) else []
        if isinstance(connections, list) and any(
            isinstance(conn, dict) for conn in connections
        ):
            active.add(cartridge)
    return active


async def installed_scoped_app_cartridges(
    user: dict | None,
    candidate_cartridges: set[str] | None,
    *,
    scope_resolver: WorkspaceScopeResolver,
    get_db_pool: DbPoolFactory,
    scoped_db_for_user: ScopedDbForUser,
    scoped_user_factory: ScopedUserFactory,
    context_visible_cartridges: VisibleCartridges,
) -> set[str]:
    candidates = normalize_candidate_cartridges(candidate_cartridges)
    if not candidates:
        return set()
    tenant_id, workspace_id = await scope_resolver(user)
    if not tenant_id or not workspace_id:
        return set()
    pool = await get_db_pool()
    scoped_user = scoped_user_factory(user, tenant_id, workspace_id)
    async with scoped_db_for_user(pool, scoped_user or {}) as (conn, _, _):
        rows = await conn.fetch(
            """
            SELECT ci.cartridge_id
              FROM cartridge_installations ci
              LEFT JOIN tenant_entitlements te
                ON te.tenant_id = ci.tenant_id
               AND te.workspace_id = ci.workspace_id
               AND te.cartridge_id = ci.cartridge_id
             WHERE ci.tenant_id = $1::uuid
               AND ci.workspace_id = $2::uuid
               AND ci.cartridge_id = ANY($3::text[])
               AND ci.status IN ('ready', 'active', 'installed')
               AND COALESCE(te.status, 'active') = 'active'
            """,
            tenant_id,
            workspace_id,
            sorted(candidates),
        )
    installed = {
        str(row["cartridge_id"]).strip()
        for row in rows
        if str(row["cartridge_id"] or "").strip()
    }
    visible = context_visible_cartridges(user)
    if visible is not None:
        installed &= visible
    return installed


async def workspace_scope_for_apps_filter(
    user: dict | None,
    *,
    context_factory: ContextFactory,
    workspace_memberships: WorkspaceMemberships,
    get_db_pool: DbPoolFactory,
    role_admin: str,
    logger: logging.Logger,
) -> tuple[str, str]:
    if not user:
        return "", ""
    ctx = context_factory(user)
    tenant_id = str(
        ctx.get("tenant_id")
        or user.get("active_tenant_id")
        or user.get("tenant_id")
        or ""
    ).strip()
    workspace_id = str(
        ctx.get("workspace_id")
        or user.get("active_workspace_id")
        or user.get("workspace_id")
        or ""
    ).strip()

    workspaces = user.get("workspaces")
    if (not tenant_id or not workspace_id) and not isinstance(workspaces, list):
        user_id = user.get("id")
        if user_id is not None:
            try:
                workspaces = await workspace_memberships(int(user_id))
            except Exception:
                logger.debug(
                    "Failed to resolve user workspaces for scoped apps filter",
                    exc_info=True,
                )
                workspaces = []

    if (
        (not tenant_id or not workspace_id)
        and isinstance(workspaces, list)
        and workspaces
    ):
        active = None
        if workspace_id:
            active = next(
                (
                    w
                    for w in workspaces
                    if str(w.get("workspace_id") or "") == workspace_id
                ),
                None,
            )
        if active is None:
            active = workspaces[0]
        tenant_id = tenant_id or str(active.get("tenant_id") or "").strip()
        workspace_id = workspace_id or str(active.get("workspace_id") or "").strip()

    if workspace_id and not tenant_id:
        try:
            pool = await get_db_pool()
            tenant_id = str(
                await pool.fetchval(
                    "SELECT tenant_id::text FROM workspaces WHERE id = $1::uuid",
                    workspace_id,
                )
                or ""
            ).strip()
        except Exception:
            logger.debug(
                "Failed to resolve tenant from workspace for scoped apps filter",
                exc_info=True,
            )

    if (not tenant_id or not workspace_id) and str(
        (user or {}).get("role") or ""
    ).lower() in {"owner", "super_admin", role_admin}:
        try:
            pool = await get_db_pool()
            row = await pool.fetchrow(
                """
                SELECT d.workspace_id::text AS workspace_id, w.tenant_id::text AS tenant_id
                  FROM datasets d
                  JOIN workspaces w ON w.id = d.workspace_id
                 WHERE d.layer = 'gold'
                   AND d.workspace_id IS NOT NULL
                   AND COALESCE(d.row_count, 0) > 0
                 ORDER BY d.updated_at DESC NULLS LAST,
                          d.last_refresh DESC NULLS LAST,
                          d.created_at DESC NULLS LAST
                 LIMIT 1
                """
            )
            if row:
                tenant_id = tenant_id or str(row["tenant_id"] or "").strip()
                workspace_id = workspace_id or str(row["workspace_id"] or "").strip()
        except Exception:
            logger.debug(
                "Failed to resolve fallback Gold workspace for scoped apps filter",
                exc_info=True,
            )

    return tenant_id, workspace_id


def resolve_scoped_operation_cartridge(
    user: dict | None,
    cartridge: str | None,
    *,
    active: set[str],
    allowed: set[str] | None,
    fallback: str,
    candidates: set[str],
    require_workspace_scope: RequireUserAccess,
    require_cartridge_visible: RequireCartridgeAccess,
) -> tuple[str, set[str]]:
    requested = str(cartridge or "").strip()
    candidate_set = candidates
    if active:
        if requested:
            if requested in active:
                return requested, active
            raise HTTPException(
                403, f"cartridge '{requested}' is not active for this workspace"
            )
        if fallback in active:
            return fallback, active
        return sorted(active)[0], active

    if allowed is not None:
        require_workspace_scope(user)
        allowed_candidates = {c for c in allowed if c in candidate_set}
        if requested:
            if requested in allowed_candidates:
                return requested, active
            raise HTTPException(
                403, f"cartridge '{requested}' is not installed for this workspace"
            )
        if fallback in allowed_candidates:
            return fallback, active
        if allowed_candidates:
            return sorted(allowed_candidates)[0], active
        raise HTTPException(403, "no cartridge installed for this workspace")

    resolved = requested or fallback
    if resolved:
        require_cartridge_visible(user, resolved)
    return resolved, active


def resolve_scoped_config_cartridge(
    user: dict | None,
    cartridge: str | None,
    *,
    visible: set[str] | None,
    is_workspace_scoped: bool,
    fallback: str,
    candidates: set[str],
    require_cartridge_visible: RequireCartridgeAccess,
) -> str:
    requested = str(cartridge or "").strip()
    candidate_set = candidates
    if visible is not None:
        if not is_workspace_scoped:
            raise HTTPException(403, "tenant/workspace scope required")
        visible_candidates = {c for c in visible if c in candidate_set}
        if requested:
            if requested in visible_candidates:
                return requested
            raise HTTPException(
                403, f"cartridge '{requested}' is not installed for this workspace"
            )
        if fallback in visible_candidates:
            return fallback
        if visible_candidates:
            return sorted(visible_candidates)[0]
        raise HTTPException(403, "no cartridge installed for this workspace")

    resolved = requested or fallback
    if resolved:
        require_cartridge_visible(user, resolved)
    return resolved


def scope_catalog_cartridge_arg(
    user: dict | None,
    cartridge: str | None,
    *,
    active: set[str],
    allowed: set[str] | None,
    candidates: set[str],
    fallback: str = "sap_successfactors",
    require_workspace_scope: RequireUserAccess,
    require_cartridge_visible: RequireCartridgeAccess,
) -> str:
    requested = str(cartridge or "").strip()
    if active:
        if requested:
            if requested in active:
                return requested
            raise HTTPException(
                403, f"cartridge '{requested}' is not active for this workspace"
            )
        if fallback in active:
            return fallback
        return sorted(active)[0]

    if allowed is not None:
        require_workspace_scope(user)
        allowed_candidates = {c for c in allowed if c in candidates}
        if requested:
            if requested in allowed_candidates:
                return requested
            raise HTTPException(
                403, f"cartridge '{requested}' is not installed for this workspace"
            )
        if fallback in allowed_candidates:
            return fallback
        return sorted(allowed_candidates)[0] if allowed_candidates else ""

    if requested:
        require_cartridge_visible(user, requested)
    return requested
