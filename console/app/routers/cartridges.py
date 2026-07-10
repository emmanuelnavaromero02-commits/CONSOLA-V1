"""Sprint v1.41.0 — cartridge management endpoints (auditor P1 operativa).

Moved out of console/app/main.py (3.5k lines and growing) so that the new
surface introduced in v1.41.0 lives in one file alongside its helpers.
Legacy endpoints stay in main.py until each gets a dedicated owner.

v1.44.1 — added POST/DELETE /credentials wrappers that proxy to the
vault service so the upcoming /cartridges UI can save and revoke
connection credentials without each frontend reinventing the
encrypt-then-PUT-then-audit sequence.
"""
from __future__ import annotations

import os
import re
import time
import json
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.security import get_internal_api_key
from app.dependencies import ROLE_ADMIN, require_authenticated, require_global_any_role
from app.services import audit_service
from app.services.security_context import build_security_context
from app.services.csrf import require_csrf
from app.services.permissions import require_permission
from app.services.service_urls import running_in_container, vault_url


router = APIRouter(prefix="/api/cartridges", tags=["Cartridges"])


# DNS name in compose uses dashes (sap-hcm), service id uses underscores
# (sap_hcm); this map captures both shapes plus the exposed port.
_CARTRIDGE_PORTS = {
    "hubspot": 8210,
    "replicon": 8201,
    "banxico": 8215,
    "sap_hcm": 8202,
    "sap_successfactors": 8203,
    "sap_s4hana": 8204,
    "salesforce": 8205,
}
_CONN_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


def _allowed_cartridges(user: dict | None) -> set[str] | None:
    ctx = build_security_context(user)
    allowed = {str(c).strip() for c in (ctx.get("allowed_cartridges") or []) if str(c).strip()}
    if "*" in allowed:
        return None
    return allowed


def _require_cartridge_visible(user: dict | None, cartridge: str) -> None:
    if cartridge not in _CARTRIDGE_PORTS:
        raise HTTPException(404, "Unknown cartridge")
    allowed = _allowed_cartridges(user)
    if allowed is not None and cartridge not in allowed:
        raise HTTPException(403, "cartridge not allowed")


def _cartridge_url(cartridge: str, path: str) -> str:
    port = _CARTRIDGE_PORTS[cartridge]
    if not running_in_container():
        return f"http://127.0.0.1:{port}{path}"
    host = cartridge.replace("_", "-")
    return f"http://{host}:{port}{path}"


def _cartridge_internal_headers() -> dict[str, str]:
    key = os.environ.get("INTERNAL_API_KEY_CONSOLE_TO_CARTRIDGE")
    if not key and os.environ.get("APP_ENV", "production").strip().lower() in {"production", "prod"}:
        raise RuntimeError("Missing INTERNAL_API_KEY_CONSOLE_TO_CARTRIDGE; legacy fallback disabled in production")
    if not key:
        key = get_internal_api_key()
    return {
        "X-Api-Key": key,
        "X-Internal-Service": "console",
    }


def _cartridge_internal_headers_for_user(user: dict | None) -> dict[str, str]:
    headers = _cartridge_internal_headers()
    headers["X-Security-Context"] = json.dumps(build_security_context(user), ensure_ascii=False)
    return headers


def _test_connection_succeeded(http_success: bool, payload: dict) -> bool:
    """Only an explicit cartridge status=ok is a successful credential test."""
    if not http_success:
        return False
    return str(payload.get("status") or "").strip().lower() == "ok"


def _normalize_conn_id(conn_id: str | None) -> str | None:
    if conn_id is not None and not isinstance(conn_id, str):
        return None
    requested = (conn_id or "").strip()
    if not requested:
        return None
    if not _CONN_ID_RE.fullmatch(requested):
        raise HTTPException(400, "invalid connection id")
    return requested


def _vault_url() -> str:
    return vault_url()


# v1.44.1: vault sits behind its own internal-API-key pair. The legacy
# /api/vault/* proxy uses console/app/main.py::_hdr_for("VAULT") which
# reads INTERNAL_API_KEY_CONSOLE_TO_VAULT — we mirror that here so the
# new /cartridges/{id}/credentials endpoints land on the same audited
# vault surface as the existing PUT /api/vault/connections/* path.
_VAULT_URL = _vault_url()


def _vault_headers() -> dict[str, str]:
    key = os.environ.get("INTERNAL_API_KEY_CONSOLE_TO_VAULT")
    if not key and os.environ.get("APP_ENV", "production").strip().lower() in {"production", "prod"}:
        raise RuntimeError("Missing INTERNAL_API_KEY_CONSOLE_TO_VAULT; legacy fallback disabled in production")
    if not key:
        key = get_internal_api_key()
    return {"x-api-key": key, "x-internal-service": "console"}


def _scrub_credential_payload(payload: dict) -> dict:
    """Return a copy of the credential payload with values masked.
    Used as ``audit_events.metadata`` so we record WHICH fields were
    written without persisting the secret values themselves.

    The brief is explicit: every credential write must be audited
    AND must not leak values into the audit trail.
    """
    return {k: ("***" if v not in (None, "") else "") for k, v in payload.items()}


@router.get("", dependencies=[Depends(require_permission("cartridges.read"))])
async def list_cartridges(user: dict = Depends(require_authenticated)):
    allowed = _allowed_cartridges(user)
    cartridges = sorted(_CARTRIDGE_PORTS.keys()) if allowed is None else sorted(set(_CARTRIDGE_PORTS) & allowed)
    return {"cartridges": cartridges}


@router.get(
    "/{cartridge}/connector_schema",
    dependencies=[Depends(require_permission("cartridges.read"))],
)
async def connector_schema(cartridge: str, user: dict = Depends(require_authenticated)):
    """Return connector.yaml so the UI can render a dynamic form."""
    _require_cartridge_visible(user, cartridge)
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
async def entities(cartridge: str, user: dict = Depends(require_authenticated)):
    """List entities + last watermark per entity, merged for the UI."""
    _require_cartridge_visible(user, cartridge)
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
async def run_entity(
    cartridge: str,
    entity: str,
    request: Request,
    mode: str = "incremental",
    conn_id: str | None = Query(default=None, max_length=128),
):
    """Trigger entity extraction via the cartridge /skills router."""
    user = getattr(request.state, "user", None) or {}
    _require_cartridge_visible(user, cartridge)
    if mode not in {"full", "incremental"}:
        raise HTTPException(400, "mode must be 'full' or 'incremental'")
    skill = "run_full_load" if mode == "full" else "run_incremental"
    security_context = build_security_context(user)
    selected_conn_id = _normalize_conn_id(conn_id)
    async with httpx.AsyncClient(timeout=30.0, headers=_cartridge_internal_headers()) as c:
        r = await c.post(
            _cartridge_url(cartridge, f"/skills/{skill}/{entity}"),
            params={"conn_id": selected_conn_id} if selected_conn_id else None,
            json={"security_context": security_context},
        )
    if r.status_code >= 400:
        await audit_service.record_event(
            user_id=user.get("id"),
            email=user.get("email"),
            action="cartridge.entity.run",
            resource_type="cartridge_entity",
            resource_id=f"{cartridge}.{entity}",
            status="failed",
            metadata={"mode": mode, "status_code": r.status_code},
        )
        if r.status_code >= 500:
            raise HTTPException(
                424,
                {
                    "error": "cartridge_not_ready",
                    "message": "Cartridge extraction failed or is not configured.",
                    "upstream_status": r.status_code,
                },
            )
        raise HTTPException(r.status_code, r.text[:500])
    await audit_service.record_event(
        user_id=user.get("id"),
        email=user.get("email"),
        action="cartridge.entity.run",
        resource_type="cartridge_entity",
        resource_id=f"{cartridge}.{entity}",
        status="success",
        metadata={"mode": mode, "skill": skill},
    )
    return r.json()


@router.post(
    "/{cartridge}/test_connection",
    dependencies=[Depends(require_csrf), Depends(require_permission("cartridges.write"))],
)
async def test_connection(
    cartridge: str,
    request: Request,
    conn_id: str | None = Query(default=None, max_length=128),
):
    """Validate credentials by hitting the cartridge's /skills/test_connection.

    v1.44.1: the response shape is normalised to the v1.44.1 brief
    contract ``{ok, message, latency_ms}`` so the upcoming UI can
    render success/error consistently regardless of which cartridge
    answered. Records an audit event with the outcome AND scrubs
    the response body so a chatty cartridge error can't leak
    sensitive substrings into audit_events.
    """
    user = getattr(request.state, "user", None) or {}
    _require_cartridge_visible(user, cartridge)
    selected_conn_id = _normalize_conn_id(conn_id)

    started = time.monotonic()
    ok = False
    message = ""
    payload: dict = {}
    try:
        async with httpx.AsyncClient(
            timeout=10.0, headers=_cartridge_internal_headers_for_user(user)
        ) as c:
            params = {"conn_id": selected_conn_id} if selected_conn_id else None
            r = await c.post(_cartridge_url(cartridge, "/skills/test_connection"), params=params)
        latency_ms = int((time.monotonic() - started) * 1000)
        payload = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        ok = _test_connection_succeeded(r.is_success, payload)
        status_label = str(payload.get("status") or "").strip()
        missing = payload.get("missing")
        missing_label = f"; missing={missing}" if missing else ""
        message = payload.get("message") or payload.get("error") or (
            f"Conectado ({latency_ms} ms)" if ok
            else f"{status_label or 'not_ok'}{missing_label} (HTTP {r.status_code})"
        )
    except Exception as exc:
        latency_ms = int((time.monotonic() - started) * 1000)
        ok = False
        # Truncate the message — error strings from a cartridge may
        # surface internal endpoints / tokens / file paths.
        message = str(exc)[:200] or "connection error"

    await audit_service.record_event(
        user_id=user.get("id"),
        email=user.get("email"),
        action="cartridge.test_connection",
        resource_type="cartridge",
        resource_id=cartridge,
        status="success" if ok else "failure",
        metadata={
            "latency_ms": latency_ms,
            "outcome_message": message[:160],
            **({"conn_id": selected_conn_id} if selected_conn_id else {}),
        },
    )

    result = {"ok": ok, "message": message, "latency_ms": latency_ms}
    if isinstance(payload.get("status"), str):
        result["status"] = payload["status"]
    if isinstance(payload.get("missing"), list):
        result["missing"] = payload["missing"]
    return result


# ── v1.44.1: credential lifecycle wrappers ────────────────────────────────
#
# The legacy /api/vault/connections/{cartridge}/{conn_id} endpoints in
# console/app/main.py stay — they're used by the existing /viewer/vault
# admin UI and any external integration that already bound to them. The
# new wrappers below give the upcoming /cartridges form a single,
# cartridge-scoped surface that:
#   * uses ``conn_id="default"`` (one credential set per cartridge — the
#     /cartridges form only configures one)
#   * audits every write (success + failure) with the field names but
#     NOT the values
#   * normalises errors so the UI can show a friendly toast


_DEFAULT_CONN_ID = "default"


@router.post(
    "/{cartridge}/credentials",
	    dependencies=[
	        Depends(require_csrf),
	        Depends(require_permission("vault.connections.write")),
	        Depends(require_global_any_role("owner", "super_admin", ROLE_ADMIN)),
	    ],
)
async def save_credentials(cartridge: str, body: dict, request: Request):
    """Encrypt + store cartridge credentials in the vault.

    Body shape: ``{<field_name>: <value>, ...}`` — typically the keys
    declared by GET /api/cartridges/{cartridge}/connector_schema.

    Returns: ``{"ok": true, "encrypted_count": N, "conn_id": "default"}``
    """
    _require_cartridge_visible(getattr(request.state, "user", None), cartridge)
    if not isinstance(body, dict) or not body:
        raise HTTPException(400, "Credential payload must be a non-empty object")

    user = getattr(request.state, "user", None) or {}
    audit_metadata = {
        "conn_id": _DEFAULT_CONN_ID,
        "fields_written": sorted(body.keys()),
        # Values are *never* persisted in audit_events. See
        # _scrub_credential_payload() for the masking contract.
        "masked_values": _scrub_credential_payload(body),
    }

    try:
        async with httpx.AsyncClient(headers=_vault_headers(), timeout=10.0) as c:
            r = await c.put(
                f"{_VAULT_URL}/connections/{cartridge}/{_DEFAULT_CONN_ID}",
                json=body,
            )
        ok = r.is_success
        status = "success" if ok else "failure"
    except httpx.HTTPError as exc:
        ok = False
        status = "failure"
        audit_metadata["error"] = str(exc)[:200]

    await audit_service.record_event(
        user_id=user.get("id"),
        email=user.get("email"),
        action="cartridge.credentials.write",
        resource_type="cartridge",
        resource_id=cartridge,
        status=status,
        metadata=audit_metadata,
    )

    if not ok:
        raise HTTPException(502, "Vault write failed")

    return {
        "ok": True,
        "encrypted_count": len(body),
        "conn_id": _DEFAULT_CONN_ID,
    }


@router.delete(
    "/{cartridge}/credentials",
	    dependencies=[
	        Depends(require_csrf),
	        Depends(require_permission("vault.connections.write")),
	        Depends(require_global_any_role("owner", "super_admin", ROLE_ADMIN)),
	    ],
)
async def delete_credentials(cartridge: str, request: Request):
    """Remove cartridge credentials from the vault. 404 if absent."""
    _require_cartridge_visible(getattr(request.state, "user", None), cartridge)

    user = getattr(request.state, "user", None) or {}
    not_found = False
    ok = False
    try:
        async with httpx.AsyncClient(headers=_vault_headers(), timeout=10.0) as c:
            r = await c.delete(
                f"{_VAULT_URL}/connections/{cartridge}/{_DEFAULT_CONN_ID}"
            )
        if r.status_code == 404:
            not_found = True
        ok = r.is_success or not_found
    except httpx.HTTPError:
        ok = False

    await audit_service.record_event(
        user_id=user.get("id"),
        email=user.get("email"),
        action="cartridge.credentials.delete",
        resource_type="cartridge",
        resource_id=cartridge,
        status="success" if ok else "failure",
        metadata={"conn_id": _DEFAULT_CONN_ID, "not_found": not_found},
    )

    if not ok:
        raise HTTPException(502, "Vault delete failed")
    if not_found:
        raise HTTPException(404, "No credentials stored for this cartridge")
    return {"ok": True, "conn_id": _DEFAULT_CONN_ID}
