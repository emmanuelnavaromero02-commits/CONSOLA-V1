from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from app.domains.iam.access_payload import me_access_payload
from app.services import permissions as perms


async def me_access_response(
    *,
    user: dict[str, Any],
    fetch_cartridge_access: Callable[..., Awaitable[tuple[list[dict], list[dict]]]],
    cartridge_pool_factory: Callable[..., Awaitable[Any]],
    logger: Any,
) -> dict[str, Any]:
    effective = sorted(perms.get_effective_permissions(user))
    role_canonical = perms.canonical_role(user.get("role"))
    workspace_role_resolved = perms.workspace_role(user) or None

    cartridges_allowed, cartridges_denied = await fetch_cartridge_access(
        user,
        pool_factory=cartridge_pool_factory,
        logger=logger,
    )

    return me_access_payload(
        user,
        effective_permissions=effective,
        role_canonical=role_canonical,
        workspace_role_resolved=workspace_role_resolved,
        cartridges_allowed=cartridges_allowed,
        cartridges_denied=cartridges_denied,
    )
