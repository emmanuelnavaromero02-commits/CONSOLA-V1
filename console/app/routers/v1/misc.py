from __future__ import annotations

from fastapi import APIRouter
import types

import app.main as _console_main

# Import the current console runtime namespace, including private helper
# functions used by legacy handlers. Handlers are rebound to app.main's
# namespace before registration so existing tests and monkeypatches that
# patch app.main.<helper> continue to affect the handler at runtime.
globals().update(_console_main.__dict__)
router = APIRouter()


def _bind_to_main(fn):
    rebound = types.FunctionType(
        fn.__code__,
        _console_main.__dict__,
        fn.__name__,
        fn.__defaults__,
        fn.__closure__,
    )
    rebound.__kwdefaults__ = fn.__kwdefaults__
    rebound.__annotations__ = dict(getattr(fn, "__annotations__", {}))
    rebound.__dict__.update(getattr(fn, "__dict__", {}))
    rebound.__doc__ = fn.__doc__
    rebound.__module__ = _console_main.__name__
    _console_main.__dict__[fn.__name__] = rebound
    return rebound

# /api/auth/login
@router.post("/api/auth/login", dependencies=[Depends(require_csrf)])
@_bind_to_main
async def api_auth_login(request: Request, body: dict):
    # Legacy compatibility alias for clients that still post to
    # /api/auth/login. Delegate through the real handler so CSRF,
    # rate-limit, and session behavior stay identical to /auth/login.
    return await auth_login(request, body)

# /api/apps
@router.get("/api/apps", dependencies=[Depends(require_permission("apps.read"))])
@_bind_to_main
async def api_apps(user: dict = Depends(require_permission("apps.read"))):
    """List published analytic apps visible to the active scoped connections."""
    async with httpx.AsyncClient(headers=_hdr_for("REFINEMENT"), timeout=10) as c:
        r = await c.post(f"{REFINEMENT_URL}/mcp/invoke",
                         json=_mcp_payload("list_apps", {}, user))
    if r.status_code >= 400:
        raise HTTPException(r.status_code, _upstream_error_detail(r, "Apps service unavailable"))
    payload = r.json()
    active_cartridges = await _active_scoped_connection_cartridges(
        user,
        _app_payload_cartridge_candidates(payload),
    )
    return _filter_apps_payload_to_scoped_connections(payload, active_cartridges)

# /api/apps/{name}
@router.delete("/api/apps/{name}", dependencies=[Depends(require_csrf), Depends(require_any_role(ROLE_ADMIN, ROLE_WORKSPACE_ADMIN))])
@_bind_to_main
async def api_apps_delete(name: str, user: dict = Depends(require_authenticated)):
    """Delete a published analytic app by name."""
    async with httpx.AsyncClient(headers=_hdr_for("REFINEMENT"), timeout=10) as c:
        r = await c.post(f"{REFINEMENT_URL}/mcp/invoke",
                         json=_mcp_payload("delete_app", {"name": name}, user))
    payload = r.json()
    result = payload.get("result", payload)
    if not result.get("deleted"):
        raise HTTPException(404, result.get("error") or f"App '{name}' not found")
    return result
