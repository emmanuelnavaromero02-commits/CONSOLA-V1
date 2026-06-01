from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from app.dependencies import get_current_global_user, require_authenticated
from app.routers.pages import _console_next_response
from app.services import marketplace_service
from app.services.csrf import require_csrf
from app.services.permissions import require_permission


router = APIRouter(tags=["Marketplace"])


@router.get("/marketplace", dependencies=[Depends(require_permission("marketplace.read"))])
async def marketplace_page(request: Request):
    return _console_next_response(request, "marketplace/index.html")


@router.get("/customer/cartridges", dependencies=[Depends(require_permission("marketplace.read"))])
async def customer_cartridges_page(request: Request):
    return _console_next_response(request, "customer/cartridges/index.html")


@router.get(
    "/admin/installations",
    dependencies=[
        Depends(require_permission("marketplace.admin")),
    ],
)
async def admin_installations_page(request: Request):
    return _console_next_response(request, "admin/installations/index.html")


@router.get(
    "/admin/licenses",
    dependencies=[
        Depends(require_permission("marketplace.admin")),
    ],
)
async def admin_licenses_page(request: Request):
    return _console_next_response(request, "admin/licenses/index.html")


@router.get("/api/marketplace/products", dependencies=[Depends(require_permission("marketplace.read"))])
async def api_marketplace_products(user: dict = Depends(require_authenticated)):
    try:
        return await marketplace_service.list_products(user)
    except marketplace_service.MarketplaceError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/api/marketplace/products/{cartridge_id}", dependencies=[Depends(require_permission("marketplace.read"))])
async def api_marketplace_product(cartridge_id: str, user: dict = Depends(require_authenticated)):
    try:
        return {"product": await marketplace_service.get_product(cartridge_id, user)}
    except marketplace_service.MarketplaceError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/api/marketplace/installations", dependencies=[Depends(require_permission("marketplace.read"))])
async def api_marketplace_installations(user: dict = Depends(require_authenticated)):
    try:
        return await marketplace_service.list_installations(user)
    except marketplace_service.MarketplaceError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/api/customer/cartridges", dependencies=[Depends(require_permission("marketplace.read"))])
async def api_customer_cartridges(user: dict = Depends(require_authenticated)):
    try:
        return await marketplace_service.list_installations(user)
    except marketplace_service.MarketplaceError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post(
    "/api/marketplace/products/{cartridge_id}/request",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("marketplace.request")),
    ],
)
async def api_marketplace_request(cartridge_id: str, user: dict = Depends(require_authenticated)):
    try:
        return await marketplace_service.request_product(cartridge_id, user)
    except marketplace_service.MarketplaceError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post(
    "/api/marketplace/products/{cartridge_id}/activate",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("marketplace.admin")),
    ],
)
async def api_marketplace_activate(cartridge_id: str, user: dict = Depends(require_authenticated)):
    try:
        return await marketplace_service.activate_product(cartridge_id, user)
    except marketplace_service.MarketplaceError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post(
    "/api/marketplace/installations/{installation_id}/retry",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("marketplace.request")),
    ],
)
async def api_marketplace_retry(installation_id: str, user: dict = Depends(require_authenticated)):
    try:
        return await marketplace_service.retry_installation(installation_id, user)
    except marketplace_service.MarketplaceError as exc:
        message = str(exc)
        lowered = message.lower()
        status = 403 if "permission" in lowered or "access" in lowered or "allowed" in lowered else (
            409 if "approval" in lowered or "active" in lowered or "state" in lowered or "status" in lowered else 404
        )
        raise HTTPException(status, message) from exc


@router.get(
    "/api/admin/installations",
    dependencies=[
        Depends(require_permission("marketplace.admin")),
    ],
)
async def api_admin_installations(user: dict = Depends(get_current_global_user)):
    try:
        return await marketplace_service.list_admin_installations(user)
    except marketplace_service.MarketplaceError as exc:
        raise HTTPException(403, str(exc)) from exc


@router.get(
    "/api/admin/installations/{installation_id}",
    dependencies=[
        Depends(require_permission("marketplace.admin")),
    ],
)
async def api_admin_installation(installation_id: str, user: dict = Depends(get_current_global_user)):
    try:
        return await marketplace_service.get_admin_installation(installation_id, user)
    except marketplace_service.MarketplaceError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get(
    "/api/admin/installations/{installation_id}/access",
    dependencies=[
        Depends(require_permission("marketplace.admin")),
    ],
)
async def api_admin_installation_access(installation_id: str, user: dict = Depends(get_current_global_user)):
    try:
        return await marketplace_service.list_installation_access(installation_id, user)
    except marketplace_service.MarketplaceError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.patch(
    "/api/admin/installations/{installation_id}/access/{target_user_id}",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("marketplace.admin")),
    ],
)
async def api_admin_installation_user_access(
    installation_id: str,
    target_user_id: int,
    payload: dict = Body(default_factory=dict),
    user: dict = Depends(get_current_global_user),
):
    try:
        return await marketplace_service.set_installation_user_access(
            installation_id,
            target_user_id,
            payload.get("mode"),
            payload.get("reason"),
            user,
        )
    except marketplace_service.MarketplaceError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post(
    "/api/admin/installations/{installation_id}/approve",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("marketplace.admin")),
    ],
)
async def api_admin_installation_approve(installation_id: str, user: dict = Depends(get_current_global_user)):
    try:
        return await marketplace_service.approve_installation(installation_id, user)
    except marketplace_service.MarketplaceError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post(
    "/api/admin/installations/{installation_id}/pause",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("marketplace.admin")),
    ],
)
async def api_admin_installation_pause(installation_id: str, user: dict = Depends(get_current_global_user)):
    try:
        return await marketplace_service.pause_installation(installation_id, user)
    except marketplace_service.MarketplaceError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post(
    "/api/admin/installations/{installation_id}/revoke",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("marketplace.admin")),
    ],
)
async def api_admin_installation_revoke(installation_id: str, user: dict = Depends(get_current_global_user)):
    try:
        return await marketplace_service.revoke_installation(installation_id, user)
    except marketplace_service.MarketplaceError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post(
    "/api/admin/installations/{installation_id}/reactivate",
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission("marketplace.admin")),
    ],
)
async def api_admin_installation_reactivate(installation_id: str, user: dict = Depends(get_current_global_user)):
    try:
        return await marketplace_service.reactivate_installation(installation_id, user)
    except marketplace_service.MarketplaceError as exc:
        raise HTTPException(400, str(exc)) from exc
