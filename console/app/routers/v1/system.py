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

# /healthz
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
    return {
        "ok": True,
        "service": "console",
        "version": _console_version(),
        "app_env": _app_env(),
    }

# /readyz
@router.get("/readyz")
@_bind_to_main
async def readyz(request: Request):
    """Dependency-aware readiness probe.

    `/healthz` only proves the process can answer. `/readyz` is stricter:
    it verifies Postgres and core sibling services so deploy/proxy layers can
    keep traffic away from a half-started console.
    """
    checks: dict[str, dict] = {}
    checks["startup"] = _startup_readiness_status(request.app)
    if checks["startup"].get("status") != "up":
        body = {"ok": False, "service": "console"}
        if getattr(request.state, "user", None):
            body["checks"] = checks
        return JSONResponse(
            body,
            status_code=503,
        )

    try:
        pool = await _get_db_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        checks["postgres"] = {"status": "up"}
    except Exception as exc:
        logger.warning("readiness probe failed for postgres", exc_info=True)
        checks["postgres"] = {"status": "down", "error": type(exc).__name__}

    deps = {
        "refinement": (f"{REFINEMENT_URL.rstrip('/')}/healthz", "REFINEMENT"),
        "mcp-infra": (f"{os.environ.get('MCP_INFRA_URL', 'http://mcp-infra:8010').rstrip('/')}/healthz", "MCP_INFRA"),
        "vault": (f"{_vault_url()}/healthz", "VAULT"),
    }
    for name, (url, server) in deps.items():
        checks[name] = await _dependency_health(name, url, server)

    require_data = (
        os.environ.get("CONTROL_ROOM_REQUIRE_DATA_READY", "").strip().lower() in {"1", "true", "yes", "on"}
        or str(request.query_params.get("require_data") or "").strip().lower() in {"1", "true", "yes", "on"}
    )
    require_intelligence_param = str(request.query_params.get("require_intelligence") or "").strip().lower()
    intelligence_opt_out_allowed = not _is_production_env() and require_intelligence_param in {"0", "false", "no", "off"}
    require_intelligence_data = (
        os.environ.get("CONTROL_ROOM_REQUIRE_INTELLIGENCE_READY", "").strip().lower() in {"1", "true", "yes", "on"}
        or require_intelligence_param in {"1", "true", "yes", "on"}
        or (require_data and _is_production_env() and not intelligence_opt_out_allowed)
    )
    checks["control_room_data"] = await _control_room_data_check(require_data=require_data)
    try:
        from app.services.intelligence.readiness import intelligence_readiness

        checks["intelligence_data"] = await intelligence_readiness(
            getattr(request.state, "user", None),
            require_data=require_intelligence_data,
        )
    except Exception as exc:
        logger.warning("readiness probe failed for intelligence_data", exc_info=True)
        checks["intelligence_data"] = {
            "status": "degraded",
            "required": require_intelligence_data,
            "error": type(exc).__name__,
        }

    dependency_ok = all(
        check.get("status") == "up"
        for name, check in checks.items()
        if name not in {"control_room_data", "intelligence_data"}
    )
    data_ok = (
        (
            checks["control_room_data"].get("status") == "up"
            and (
                checks["intelligence_data"].get("status") == "up"
                or not require_intelligence_data
            )
        )
        or not require_data
    )
    ok = dependency_ok and data_ok
    body = {"ok": ok, "service": "console"}
    if getattr(request.state, "user", None):
        body["checks"] = checks
    return JSONResponse(
        body,
        status_code=200 if ok else 503,
    )

# /api/config
@router.get("/api/config")
@_bind_to_main
async def api_config(request: Request):
    """Runtime config (URLs only, no secrets)."""
    return {
        "workspace_url": _public_url(
            "WORKSPACE_URL",
            fallback_env="WORKSPACE_PUBLIC_URL",
            development_default="http://localhost:8001",
        ),
        "console_url": _public_url("CONSOLE_URL", development_default="http://localhost:8000"),
        "airflow_url": _public_url("AIRFLOW_PUBLIC_URL", development_default="http://localhost:8082"),
        "superset_url": _public_url("SUPERSET_PUBLIC_URL", development_default="http://localhost:8088"),
        "s3_bucket":     os.environ.get("S3_BUCKET_NAME") or os.environ.get("MINIO_BUCKET", "lakehouse"),
    }

# /api/system/info
@router.get("/api/system/info")
@_bind_to_main
async def system_info(user: dict = Depends(require_authenticated)):
    version = _console_version()
    # v1.43.2 (Frontend R1 hardening): expose ``dev_mode`` so the UI
    # can hide CTAs that gate on dev-only mcp-infra tools (Studio
    # Deploy DAG, etc). Pre-v1.43.2 the console rendered those
    # buttons unconditionally; clicking them in production now surfaces
    # a PermissionError from airflow_create_dag — which is correct but
    # confusing. The button is hidden by checking this flag.
    app_env = os.environ.get("APP_ENV", "production").lower()
    rce_tools_enabled = os.environ.get("ALLOW_RCE_TOOLS", "").strip().lower() in {"1", "true", "yes", "on"}
    dev_mode = app_env in {"development", "dev", "local", "test"}
    return {
        "version": version,
        "env": os.environ.get("MODE", "local"),
        "service": "console",
        "app_env": app_env,
        "dev_mode": dev_mode,
        "rce_tools_enabled": rce_tools_enabled,
        "dag_deploy_enabled": dev_mode and rce_tools_enabled,
    }

# /me
@router.get("/me")
@_bind_to_main
async def viewer_me(request: Request):
    require_user(request)
    from app.routers.pages import _console_next_response

    return _console_next_response(request, "me/index.html")

# /api/me
@router.get("/api/me")
@_bind_to_main
async def api_me(user: dict = Depends(require_authenticated)):
    return _user_payload(user)

# /api/me/access
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
            # Cartridges visible to this caller in their workspace.
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
                # Marketplace migrations may not be applied in every env.
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
        # If the marketplace pool is unavailable we still return the
        # identity-level info so the page can render in restricted mode.
        # Log it: silent fallback is intentional for ops resilience but
        # we must not mask repeated failures from the team.
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
        # The front-end uses these flags to decide what to render. They are
        # *display hints only*; every action endpoint enforces its own gate.
        # IMPORTANT: each flag must replicate the FULL guard chain of the
        # target page. /iam, /admin/users, /settings, /operations require
        # both the permission AND `require_admin` (global admin role).
        # If we only checked the permission, a security_admin user (who
        # has iam.users.read but is not a global admin) would see the
        # link and get a 403 on click. The backend still rejects, but the
        # UI must not lie.
        "ui_capabilities": {
            "can_view_iam":            "iam.users.read" in effective and role_canonical in {"owner", "super_admin", "admin"},
            "can_admin_marketplace":   "marketplace.admin" in effective,
            # `workspace_role()` already normalizes the legacy database
            # workspace_role values (admin/owner/super_admin/security_admin)
            # to "workspace_admin" before returning. Comparing only to
            # "workspace_admin" keeps the intent explicit and prevents a
            # future copy-paste from re-introducing a global-admin check on
            # a workspace-scoped flag.
            "can_admin_workspace":     workspace_role_resolved in {"workspace_admin", "tenant_admin"},
            "can_view_audit":          "security.audit.read" in effective,
            "can_view_sessions":       "security.sessions.read" in effective,
        },
    }

# /api/me/change-password
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
    # Rotate CSRF after a successful self-service password change.
    set_csrf_cookie(resp)
    return resp

# /tokens/summary
@router.get("/tokens/summary")
@_bind_to_main
async def tokens_summary(user: dict = Depends(require_permission("copilot.use"))):
    return await token_store.summary(user_context=user)
