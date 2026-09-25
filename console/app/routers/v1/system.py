from __future__ import annotations

from fastapi import APIRouter
import types

import app.main as _console_main

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

@router.get("/healthz")
@_bind_to_main
async def healthz():
    """Sprint v1.21 (F2): liveness probe for the compose healthcheck.
    No auth, no DB call — answers as long as the FastAPI event loop is
    running. Used by infra/docker-compose.yml so dependent services
    wait on service_healthy instead of service_started, avoiding the
    boot race where console answers before its lifespan has wired the
    DB pool.

    Carries ``version`` + ``app_env`` (no secrets) so an operator can
    confirm WHAT is deployed without authenticating — useful for the
    beta/demo runbook's health checks."""
    return _healthz_payload(version=_console_version(), app_env=_app_env())

@router.get("/readyz")
@_bind_to_main
async def readyz(request: Request):
    """Dependency-aware readiness probe.

    `/healthz` only proves the process can answer. `/readyz` is stricter:
    it verifies Postgres and core sibling services so deploy/proxy layers can
    keep traffic away from a half-started console.
    """
    checks, ok = await _build_readyz_checks_impl(
        app=request.app,
        query_params=request.query_params,
        authenticated_user=getattr(request.state, "user", None),
        environ=os.environ,
        refinement_url=REFINEMENT_URL,
        mcp_infra_url=os.environ.get("MCP_INFRA_URL", "http://mcp-infra:8010"),
        vault_url=_vault_url,
        startup_readiness_status=_startup_readiness_status,
        get_db_pool=_get_db_pool,
        dependency_health=_dependency_health,
        control_room_data_check=_control_room_data_check,
        intelligence_readiness=_readyz_intelligence_readiness,
        is_production_env=_is_production_env,
        warn=logger.warning,
    )
    body = {"ok": ok, "service": "console"}
    if getattr(request.state, "user", None):
        body["checks"] = checks
    return JSONResponse(
        body,
        status_code=200 if ok else 503,
    )

@router.get("/api/config")
@_bind_to_main
async def api_config(request: Request):
    """Runtime config (URLs only, no secrets)."""
    return _runtime_config_payload(os.environ, public_url=_public_url)

@router.get("/api/system/info")
@_bind_to_main
async def system_info(user: dict = Depends(require_authenticated)):
    return _system_info_payload(os.environ, version=_console_version())

@router.get("/me")
@_bind_to_main
async def viewer_me(request: Request):
    require_user(request)
    from app.routers.pages import _console_next_response

    return _console_next_response(request, "me/index.html")

@router.get("/api/me")
@_bind_to_main
async def api_me(user: dict = Depends(require_authenticated)):
    return _user_payload(user)

@router.get("/api/me/access")
@_bind_to_main
async def api_me_access(user: dict = Depends(require_authenticated)):
    """Phase-0 SaaS-controls: a single endpoint that returns the user's
    *effective* security profile so the front-end can render "Mis accesos"
    without the user (or operator) inferring permissions from role names.

    The shape is intentionally narrow: the front-end uses these fields to
    decide what to render and what to disable. The backend is still the
    source of truth — every action endpoint enforces its own permission.
    """
    from app.services import permissions as _perms

    effective = sorted(_perms.get_effective_permissions(user))
    role_canonical = _perms.canonical_role(user.get("role"))
    workspace_role_resolved = _perms.workspace_role(user) or None

    cartridges_allowed: list[str] = []
    cartridges_denied: list[dict[str, str]] = []
    try:
        p = await cartridge_service.pool()
        async with p.acquire() as conn:  # noqa: SIM117 — nested try/except is intentional
            try:
                allowed_rows = await conn.fetch(
                    """
                    SELECT ci.cartridge_id, ci.status,
                           COALESCE(p.name, ci.cartridge_id) AS product_name
                      FROM cartridge_installations ci
                      LEFT JOIN marketplace_products p ON p.cartridge_id = ci.cartridge_id
                     WHERE ci.tenant_id = $1
                       AND ci.workspace_id = $2
                       AND ci.status IN ('ready', 'active')
                       AND NOT EXISTS (
                         SELECT 1
                           FROM user_cartridge_overrides uco
                          WHERE uco.tenant_id = ci.tenant_id
                            AND uco.workspace_id = ci.workspace_id
                            AND uco.cartridge_id = ci.cartridge_id
                            AND uco.user_id = $3
                            AND uco.mode = 'deny'
                       )
                    ORDER BY product_name
                    """,
                    user.get("tenant_id") or user.get("active_tenant_id"),
                    user.get("workspace_id") or user.get("active_workspace_id"),
                    user.get("id"),
                )
                cartridges_allowed = [
                    {"cartridge_id": r["cartridge_id"],
                     "product_name": r["product_name"],
                     "status": r["status"]}
                    for r in allowed_rows
                ]
            except Exception:  # noqa: BLE001
                cartridges_allowed = []
            try:
                denied_rows = await conn.fetch(
                    """
                    SELECT uco.cartridge_id, uco.mode,
                           ci.status AS installation_status,
                           COALESCE(p.name, uco.cartridge_id) AS product_name
                      FROM user_cartridge_overrides uco
                      LEFT JOIN cartridge_installations ci
                        ON ci.tenant_id = uco.tenant_id
                       AND ci.workspace_id = uco.workspace_id
                       AND ci.cartridge_id = uco.cartridge_id
                      LEFT JOIN marketplace_products p
                        ON p.cartridge_id = uco.cartridge_id
                     WHERE uco.tenant_id = $1
                       AND uco.workspace_id = $2
                       AND uco.user_id = $3
                       AND uco.mode = 'deny'
                    ORDER BY product_name
                    """,
                    user.get("tenant_id") or user.get("active_tenant_id"),
                    user.get("workspace_id") or user.get("active_workspace_id"),
                    user.get("id"),
                )
                cartridges_denied = [
                    {"cartridge_id": r["cartridge_id"],
                     "product_name": r["product_name"],
                     "reason": "user_deny",
                     "installation_status": r["installation_status"]}
                    for r in denied_rows
                ]
            except Exception:  # noqa: BLE001
                cartridges_denied = []
    except Exception:  # noqa: BLE001
        logger.warning("api_me_access: marketplace pool unavailable", exc_info=True)

    return {
        "user": {
            "id": user.get("id"),
            "email": user.get("email"),
            "name": user.get("name") or user.get("email"),
        },
        "role": {
            "global": role_canonical,
            "is_platform_admin": role_canonical in {"owner", "super_admin", "admin"},
        },
        "workspace": {
            "tenant_id": user.get("tenant_id") or user.get("active_tenant_id"),
            "workspace_id": user.get("workspace_id") or user.get("active_workspace_id"),
            "workspace_role": workspace_role_resolved,
        },
        "permissions": effective,
        "cartridges": {
            "allowed": cartridges_allowed,
            "denied": cartridges_denied,
        },
        "ui_capabilities": {
            "can_view_iam":            "iam.users.read" in effective and role_canonical in {"owner", "super_admin", "admin"},
            "can_admin_marketplace":   "marketplace.admin" in effective,
            "can_admin_workspace":     workspace_role_resolved in {"workspace_admin", "tenant_admin"},
            "can_view_audit":          "security.audit.read" in effective,
            "can_view_sessions":       "security.sessions.read" in effective,
        },
    }

@router.post("/api/me/change-password", dependencies=[Depends(require_csrf)])
@_bind_to_main
async def api_me_change_password(body: dict, user: dict = Depends(require_authenticated)):
    current = body.get("current_password") or ""
    new     = body.get("new_password") or ""
    if not current or not new:
        raise HTTPException(400, "current_password and new_password are required")
    ok, err = await _auth.change_own_password(user["id"], current, new)
    if not ok:
        raise HTTPException(400, err or "password change failed")
    resp = JSONResponse({"changed": True})
    set_csrf_cookie(resp)
    return resp

@router.get("/tokens/summary")
@_bind_to_main
async def tokens_summary(user: dict = Depends(require_permission("copilot.use"))):
    return await token_store.summary(user_context=user)
