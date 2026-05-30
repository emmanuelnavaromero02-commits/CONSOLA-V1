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

# /api/vault/connections/{cartridge}
@router.get("/api/vault/connections/{cartridge}", dependencies=[Depends(require_permission("vault.connections.read"))])
@_bind_to_main
async def api_vault_list_connections(cartridge: str, user: dict = Depends(require_authenticated)):
    _require_cartridge_visible(user, cartridge)
    try:
        async with httpx.AsyncClient(headers=_hdr_for("VAULT"), timeout=5) as c:
            r = await c.get(f"{_VAULT_URL}/connections/{cartridge}")
        if r.status_code in (404, 204):
            return {"connections": []}
        if r.status_code >= 500:
            raise HTTPException(502, "Vault request failed")
        r.raise_for_status()
        data = r.json()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(exc.response.status_code, "Vault request failed") from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(502, "Vault request failed") from exc
    if not data:
        return {"connections": []}
    return data

# /api/vault/connections/{cartridge}/{conn_id}/reveal
@router.get("/api/vault/connections/{cartridge}/{conn_id}/reveal", dependencies=[Depends(require_permission("vault.secrets.reveal"))])
@_bind_to_main
async def api_vault_reveal_connection(cartridge: str, conn_id: str, user: dict = Depends(require_authenticated)):
    """Returns full credentials including token (not masked)."""
    _require_cartridge_visible(user, cartridge)
    async with httpx.AsyncClient(headers=_hdr_for("VAULT"), timeout=5) as c:
        r = await c.get(f"{_VAULT_URL}/connections/{cartridge}/{conn_id}")
        if r.status_code == 404:
            raise HTTPException(404, "Not found")
        r.raise_for_status()
        return r.json()

# /api/vault/connections/{cartridge}/{conn_id}
@router.put("/api/vault/connections/{cartridge}/{conn_id}", dependencies=[Depends(require_csrf), Depends(require_permission("vault.connections.write")), Depends(require_global_any_role("owner", "super_admin", ROLE_ADMIN))])
@_bind_to_main
async def api_vault_upsert_connection(cartridge: str, conn_id: str, body: dict, user: dict = Depends(require_authenticated)):
    _require_cartridge_visible(user, cartridge)
    async with httpx.AsyncClient(headers=_hdr_for("VAULT"), timeout=5) as c:
        r = await c.put(f"{_VAULT_URL}/connections/{cartridge}/{conn_id}", json=body)
        r.raise_for_status()
        return r.json()

# /api/vault/connections/{cartridge}/{conn_id}
@router.delete("/api/vault/connections/{cartridge}/{conn_id}", dependencies=[Depends(require_csrf), Depends(require_permission("vault.connections.write")), Depends(require_global_any_role("owner", "super_admin", ROLE_ADMIN))])
@_bind_to_main
async def api_vault_delete_connection(cartridge: str, conn_id: str, user: dict = Depends(require_authenticated)):
    _require_cartridge_visible(user, cartridge)
    async with httpx.AsyncClient(headers=_hdr_for("VAULT"), timeout=5) as c:
        r = await c.delete(f"{_VAULT_URL}/connections/{cartridge}/{conn_id}")
        if r.status_code == 404:
            raise HTTPException(404, "Not found")
        r.raise_for_status()
        return r.json()

# /api/vault/secrets/{scope}
@router.get("/api/vault/secrets/{scope}", dependencies=[Depends(require_permission("vault.secrets.read_masked"))])
@_bind_to_main
async def api_vault_list_secrets(scope: str, user: dict = Depends(require_authenticated)):
    _require_vault_scope_visible(user, scope)
    async with httpx.AsyncClient(headers=_hdr_for("VAULT"), timeout=5) as c:
        r = await c.get(f"{_VAULT_URL}/secrets/{scope}")
        r.raise_for_status()
        return r.json()

# /api/vault/secrets/{scope}/{key}/reveal
@router.get("/api/vault/secrets/{scope}/{key}/reveal", dependencies=[Depends(require_permission("vault.secrets.reveal"))])
@_bind_to_main
async def api_vault_reveal_secret(scope: str, key: str, user: dict = Depends(require_authenticated)):
    _require_vault_scope_visible(user, scope)
    async with httpx.AsyncClient(headers=_hdr_for("VAULT"), timeout=5) as c:
        r = await c.get(f"{_VAULT_URL}/secrets/{scope}/{key}")
        if r.status_code == 404:
            raise HTTPException(404, "Not found")
        r.raise_for_status()
        return r.json()

# /api/vault/secrets/{scope}/{key}
@router.put("/api/vault/secrets/{scope}/{key}", dependencies=[Depends(require_csrf), Depends(require_permission("vault.connections.write")), Depends(require_global_any_role("owner", "super_admin", ROLE_ADMIN))])
@_bind_to_main
async def api_vault_upsert_secret(scope: str, key: str, body: dict, user: dict = Depends(require_authenticated)):
    _require_vault_scope_visible(user, scope)
    async with httpx.AsyncClient(headers=_hdr_for("VAULT"), timeout=5) as c:
        r = await c.put(f"{_VAULT_URL}/secrets/{scope}/{key}", json=body)
        r.raise_for_status()
        return r.json()

# /api/vault/secrets/{scope}/{key}
@router.delete("/api/vault/secrets/{scope}/{key}", dependencies=[Depends(require_csrf), Depends(require_permission("vault.connections.write")), Depends(require_global_any_role("owner", "super_admin", ROLE_ADMIN))])
@_bind_to_main
async def api_vault_delete_secret(scope: str, key: str, user: dict = Depends(require_authenticated)):
    _require_vault_scope_visible(user, scope)
    async with httpx.AsyncClient(headers=_hdr_for("VAULT"), timeout=5) as c:
        r = await c.delete(f"{_VAULT_URL}/secrets/{scope}/{key}")
        if r.status_code == 404:
            raise HTTPException(404, "Not found")
        r.raise_for_status()
        return r.json()
