"""Sprint v1.41.0 — cartridge management endpoints (auditor P1 operativa).

Moved out of console/app/main.py (3.5k lines and growing) so that the new
surface introduced in v1.41.0 lives in one file alongside its helpers.
Legacy endpoints stay in main.py until each gets a dedicated owner.
"""
from __future__ import annotations

import os
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException

from app.services.csrf import require_csrf
from app.services.permissions import require_permission


router = APIRouter(prefix="/api/cartridges", tags=["Cartridges"])


# DNS name in compose uses dashes (sap-hcm), service id uses underscores
# (sap_hcm); this map captures both shapes plus the exposed port.
_CARTRIDGE_PORTS = {
    "replicon": 8201,
    "sap_hcm": 8202,
    "sap_successfactors": 8203,
    "sap_s4hana": 8204,
}


def _cartridge_url(cartridge: str, path: str) -> str:
    host = cartridge.replace("_", "-")
    return f"http://{host}:{_CARTRIDGE_PORTS[cartridge]}{path}"


def _cartridge_internal_headers() -> dict[str, str]:
    return {
        "X-Api-Key": os.environ.get("INTERNAL_API_KEY", ""),
        "X-Internal-Service": "console",
    }


@router.get("", dependencies=[Depends(require_permission("cartridges.read"))])
async def list_cartridges():
    return {"cartridges": sorted(_CARTRIDGE_PORTS.keys())}


@router.get(
    "/{cartridge}/connector_schema",
    dependencies=[Depends(require_permission("cartridges.read"))],
)
async def connector_schema(cartridge: str):
    """Return connector.yaml so the UI can render a dynamic form."""
    if cartridge not in _CARTRIDGE_PORTS:
        raise HTTPException(404, "Unknown cartridge")
    import yaml
    candidates = [
        Path(f"/registry/cartridges/{cartridge}/app/config/connector.yaml"),
        Path(__file__).resolve().parents[3] / "cartridges" / cartridge / "app" / "config" / "connector.yaml",
    ]
    for path in candidates:
        if path.exists():
            return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raise HTTPException(404, "Schema not found")


@router.get(
    "/{cartridge}/entities",
    dependencies=[Depends(require_permission("cartridges.read"))],
)
async def entities(cartridge: str):
    """List entities + last watermark per entity, merged for the UI."""
    if cartridge not in _CARTRIDGE_PORTS:
        raise HTTPException(404, "Unknown cartridge")
    async with httpx.AsyncClient(timeout=15.0, headers=_cartridge_internal_headers()) as c:
        ents = await c.get(_cartridge_url(cartridge, "/skills/entities"))
        wms = await c.get(_cartridge_url(cartridge, "/skills/get_watermarks"))
    ent_list = ents.json().get("entities", []) if ents.is_success else []
    watermarks = wms.json().get("watermarks", []) if wms.is_success else []
    wm_by_entity = {w.get("entity"): w for w in watermarks if isinstance(w, dict)}
    for ent in ent_list:
        ent["watermark"] = wm_by_entity.get(ent.get("entity"))
    return {"entities": ent_list}


@router.post(
    "/{cartridge}/entities/{entity}/run",
    dependencies=[Depends(require_csrf), Depends(require_permission("cartridges.execute"))],
)
async def run_entity(cartridge: str, entity: str, mode: str = "incremental"):
    """Trigger entity extraction via the cartridge /skills router."""
    if cartridge not in _CARTRIDGE_PORTS:
        raise HTTPException(404, "Unknown cartridge")
    if mode not in {"full", "incremental"}:
        raise HTTPException(400, "mode must be 'full' or 'incremental'")
    skill = "run_full_load" if mode == "full" else "run_incremental"
    async with httpx.AsyncClient(timeout=30.0, headers=_cartridge_internal_headers()) as c:
        r = await c.post(_cartridge_url(cartridge, f"/skills/{skill}/{entity}"))
    if r.status_code >= 400:
        raise HTTPException(r.status_code, r.text[:500])
    return r.json()


@router.post(
    "/{cartridge}/test_connection",
    dependencies=[Depends(require_csrf), Depends(require_permission("cartridges.write"))],
)
async def test_connection(cartridge: str):
    """Validate credentials by hitting the cartridge's /skills/test_connection."""
    if cartridge not in _CARTRIDGE_PORTS:
        raise HTTPException(404, "Unknown cartridge")
    try:
        async with httpx.AsyncClient(timeout=10.0, headers=_cartridge_internal_headers()) as c:
            r = await c.post(_cartridge_url(cartridge, "/skills/test_connection"))
        return r.json()
    except Exception as exc:
        return {"status": "error", "message": str(exc)[:200]}
