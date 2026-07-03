from __future__ import annotations

from collections.abc import Callable

from fastapi import HTTPException


RequireUserAccess = Callable[[dict | None], None]
RequireCartridgeAccess = Callable[[dict | None, str], None]


def normalize_candidate_cartridges(candidates: set[str] | None) -> set[str]:
    return {
        str(cartridge).strip()
        for cartridge in (candidates or set())
        if str(cartridge).strip()
    }


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
