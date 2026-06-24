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

# /apps/{name}/content
@router.get("/apps/{name}/content", dependencies=[Depends(require_permission("apps.read"))])
@_bind_to_main
async def serve_app_content_proxy(
    request: Request,
    name: str,
    user: dict = Depends(require_permission("apps.read")),
):
    return await _proxy_workspace_app(request, name, content=True)

# /apps/{name}/embed
@router.get("/apps/{name}/embed", dependencies=[Depends(require_permission("apps.read"))])
@_bind_to_main
async def serve_app_embed(
    request: Request,
    name: str,
    user: dict = Depends(require_permission("apps.read")),
):
    _validate_dataset_name(name)
    _html_text, datasets_used = await _workspace_app_content_for_embed(
        request, name, user
    )
    nonce = secrets.token_urlsafe(16)
    return HTMLResponse(
        content=_app_embed_wrapper_html(name, datasets_used, nonce),
        headers={
            "Content-Security-Policy": _app_embed_csp(nonce),
            "X-Frame-Options": "SAMEORIGIN",
        },
    )

# /apps/{name}
@router.get("/apps/{name}", dependencies=[Depends(require_permission("apps.read"))])
@_bind_to_main
async def serve_app(name: str, request: Request, user: dict = Depends(require_permission("apps.read"))):
    return await _proxy_workspace_app(request, name)

# /studio
@router.get("/studio", dependencies=[Depends(require_permission("studio.read")), Depends(require_admin)])
@_bind_to_main
async def studio_page():
    return FileResponse(STATIC / "studio.html")

# /marketplace
@router.get("/marketplace", dependencies=[Depends(require_permission("marketplace.read"))])
@_bind_to_main
async def marketplace_page(request: Request):
    from app.routers.pages import _console_next_response

    return _console_next_response(request, "marketplace/index.html")

# /customer/cartridges
@router.get("/customer/cartridges", dependencies=[Depends(require_permission("marketplace.read"))])
@_bind_to_main
async def customer_cartridges_page(request: Request):
    from app.routers.pages import _console_next_response

    return _console_next_response(request, "customer/cartridges/index.html")

# /admin/installations
@router.get(
    "/admin/installations",
    dependencies=[
        Depends(require_permission("marketplace.admin")),
    ],
)
@_bind_to_main
async def admin_installations_page(request: Request):
    from app.routers.pages import _console_next_response

    return _console_next_response(request, "admin/installations/index.html")

# /admin/licenses
@router.get(
    "/admin/licenses",
    dependencies=[
        Depends(require_permission("marketplace.admin")),
    ],
)
@_bind_to_main
async def admin_licenses_page(request: Request):
    from app.routers.pages import _console_next_response

    return _console_next_response(request, "admin/licenses/index.html")

# /api/marketplace/products
@router.get("/api/marketplace/products", dependencies=[Depends(require_permission("marketplace.read"))])
@_bind_to_main
async def api_marketplace_products(user: dict = Depends(require_authenticated)):
    try:
        return await marketplace_service.list_products(user)
    except marketplace_service.MarketplaceError as exc:
        raise HTTPException(400, "marketplace products unavailable") from exc

# /api/marketplace/products/{cartridge_id}
@router.get("/api/marketplace/products/{cartridge_id}", dependencies=[Depends(require_permission("marketplace.read"))])
@_bind_to_main
async def api_marketplace_product(cartridge_id: str, user: dict = Depends(require_authenticated)):
    try:
        return {"product": await marketplace_service.get_product(cartridge_id, user)}
    except marketplace_service.MarketplaceError as exc:
        raise HTTPException(404, "marketplace product not found") from exc

# /api/marketplace/installations
@router.get("/api/marketplace/installations", dependencies=[Depends(require_permission("marketplace.read"))])
@_bind_to_main
async def api_marketplace_installations(user: dict = Depends(require_authenticated)):
    try:
        return await marketplace_service.list_installations(user)
    except marketplace_service.MarketplaceError as exc:
        raise HTTPException(400, "marketplace installations unavailable") from exc

# /api/customer/cartridges
@router.get("/api/customer/cartridges", dependencies=[Depends(require_permission("marketplace.read"))])
@_bind_to_main
async def api_customer_cartridges(user: dict = Depends(require_authenticated)):
    try:
        return await marketplace_service.list_installations(user)
    except marketplace_service.MarketplaceError as exc:
        raise HTTPException(400, "marketplace installations unavailable") from exc

# /api/marketplace/products/{cartridge_id}/request
@router.post(
    "/api/marketplace/products/{cartridge_id}/request",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("marketplace.request")),
    ],
)
@_bind_to_main
async def api_marketplace_request(cartridge_id: str, user: dict = Depends(require_authenticated)):
    try:
        return await marketplace_service.request_product(cartridge_id, user)
    except marketplace_service.MarketplaceError as exc:
        raise HTTPException(400, "marketplace request failed") from exc

# /api/marketplace/products/{cartridge_id}/activate
@router.post(
    "/api/marketplace/products/{cartridge_id}/activate",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("marketplace.admin")),
    ],
)
@_bind_to_main
async def api_marketplace_activate(cartridge_id: str, user: dict = Depends(require_authenticated)):
    try:
        return await marketplace_service.activate_product(cartridge_id, user)
    except marketplace_service.MarketplaceError as exc:
        raise HTTPException(400, "marketplace activation failed") from exc

# /api/marketplace/installations/{installation_id}/retry
@router.post(
    "/api/marketplace/installations/{installation_id}/retry",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("marketplace.request")),
    ],
)
@_bind_to_main
async def api_marketplace_retry(installation_id: str, user: dict = Depends(require_authenticated)):
    try:
        return await marketplace_service.retry_installation(installation_id, user)
    except marketplace_service.MarketplaceError as exc:
        lowered = " ".join(str(part) for part in getattr(exc, "args", ())).lower()
        status = 403 if "permission" in lowered or "access" in lowered or "allowed" in lowered else (
            409 if "approval" in lowered or "active" in lowered or "state" in lowered or "status" in lowered else 404
        )
        raise HTTPException(status, "marketplace retry failed") from exc

# /api/admin/installations
@router.get("/api/admin/installations")
@_bind_to_main
async def api_admin_installations(
    tenant_id: str | None = None,
    workspace_id: str | None = None,
    user: dict = Depends(require_permission("marketplace.admin")),
):
    try:
        return await marketplace_service.list_admin_installations(
            user,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )
    except marketplace_service.MarketplaceError as exc:
        raise HTTPException(403, "admin marketplace access denied") from exc

# /api/admin/installations/{installation_id}
@router.get("/api/admin/installations/{installation_id}")
@_bind_to_main
async def api_admin_installation(
    installation_id: str,
    tenant_id: str | None = None,
    workspace_id: str | None = None,
    user: dict = Depends(require_permission("marketplace.admin")),
):
    try:
        return await marketplace_service.get_admin_installation(
            installation_id,
            user,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )
    except marketplace_service.MarketplaceError as exc:
        raise HTTPException(404, "installation not found") from exc

# /api/admin/installations/{installation_id}/access
@router.get("/api/admin/installations/{installation_id}/access")
@_bind_to_main
async def api_admin_installation_access(
    installation_id: str,
    tenant_id: str | None = None,
    workspace_id: str | None = None,
    user: dict = Depends(require_permission("marketplace.admin")),
):
    try:
        return await marketplace_service.list_installation_access(
            installation_id,
            user,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )
    except marketplace_service.MarketplaceError as exc:
        raise HTTPException(404, "installation access not found") from exc

# /api/admin/installations/{installation_id}/access/{target_user_id}
@router.patch(
    "/api/admin/installations/{installation_id}/access/{target_user_id}",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("marketplace.admin")),
    ],
)
@_bind_to_main
async def api_admin_installation_user_access(
    installation_id: str,
    target_user_id: int,
    tenant_id: str | None = None,
    workspace_id: str | None = None,
    payload: dict = Body(default_factory=dict),
    user: dict = Depends(require_permission("marketplace.admin")),
):
    try:
        return await marketplace_service.set_installation_user_access(
            installation_id,
            target_user_id,
            payload.get("mode"),
            payload.get("reason"),
            user,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )
    except marketplace_service.MarketplaceError as exc:
        raise HTTPException(400, "installation access update failed") from exc

# /api/admin/installations/{installation_id}/approve
@router.post(
    "/api/admin/installations/{installation_id}/approve",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("marketplace.admin")),
    ],
)
@_bind_to_main
async def api_admin_installation_approve(
    installation_id: str,
    tenant_id: str | None = None,
    workspace_id: str | None = None,
    user: dict = Depends(require_permission("marketplace.admin")),
):
    try:
        return await marketplace_service.approve_installation(
            installation_id,
            user,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )
    except marketplace_service.MarketplaceError as exc:
        raise HTTPException(400, "installation approval failed") from exc

# /api/admin/installations/{installation_id}/pause
@router.post(
    "/api/admin/installations/{installation_id}/pause",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("marketplace.admin")),
    ],
)
@_bind_to_main
async def api_admin_installation_pause(
    installation_id: str,
    tenant_id: str | None = None,
    workspace_id: str | None = None,
    user: dict = Depends(require_permission("marketplace.admin")),
):
    try:
        return await marketplace_service.pause_installation(
            installation_id,
            user,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )
    except marketplace_service.MarketplaceError as exc:
        raise HTTPException(400, "installation pause failed") from exc

# /api/admin/installations/{installation_id}/revoke
@router.post(
    "/api/admin/installations/{installation_id}/revoke",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("marketplace.admin")),
    ],
)
@_bind_to_main
async def api_admin_installation_revoke(
    installation_id: str,
    tenant_id: str | None = None,
    workspace_id: str | None = None,
    user: dict = Depends(require_permission("marketplace.admin")),
):
    try:
        return await marketplace_service.revoke_installation(
            installation_id,
            user,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )
    except marketplace_service.MarketplaceError as exc:
        raise HTTPException(400, "installation revoke failed") from exc

# /api/admin/installations/{installation_id}/reactivate
@router.post(
    "/api/admin/installations/{installation_id}/reactivate",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("marketplace.admin")),
    ],
)
@_bind_to_main
async def api_admin_installation_reactivate(
    installation_id: str,
    tenant_id: str | None = None,
    workspace_id: str | None = None,
    user: dict = Depends(require_permission("marketplace.admin")),
):
    try:
        return await marketplace_service.reactivate_installation(
            installation_id,
            user,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )
    except marketplace_service.MarketplaceError as exc:
        raise HTTPException(400, "installation reactivation failed") from exc

# /viewer/pipeline
@router.get("/viewer/pipeline", dependencies=[Depends(require_permission("monitor.read"))])
@_bind_to_main
async def viewer_pipeline(request: Request):
    return _viewer_redirect(request, "pipeline")

# /viewer/vault
@router.get("/viewer/vault", dependencies=[Depends(require_permission("vault.connections.read"))])
@_bind_to_main
async def viewer_vault(request: Request):
    return _viewer_redirect(request, "vault")
