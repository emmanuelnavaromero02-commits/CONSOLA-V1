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
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.security import get_internal_api_key
from app.dependencies import ROLE_ADMIN, require_authenticated, require_global_any_role
from app.services import audit_service
from app.services.security_context import build_security_context
from app.services.csrf import require_csrf
from app.services.permissions import require_permission


router = APIRouter(prefix="/api/cartridges", tags=["Cartridges"])


# DNS name in compose uses dashes (sap-hcm), service id uses underscores
# (sap_hcm); this map captures both shapes plus the exposed port.
_CARTRIDGE_PORTS = {
    "hubspot": 8210,
    "replicon": 8201,
    "sap_hcm": 8202,
    "sap_successfactors": 8203,
    "sap_s4hana": 8204,
    "salesforce": 8205,
}
# Connection ids double as user-facing labels, so allow spaces (e.g.
# "Replicon Analytics"). They are URL-encoded wherever they hit the Vault
# path. Kept to a safe printable subset — no slashes or control chars.
_CONN_ID_RE = re.compile(r"^[\w .:-]{1,128}$")


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
    if not _running_in_container():
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


def _running_in_container() -> bool:
    return Path("/.dockerenv").exists() or bool(os.environ.get("KUBERNETES_SERVICE_HOST"))


def _service_url(env_name: str, docker_default: str, local_default: str) -> str:
    raw = os.environ.get(env_name)
    if raw:
        return raw.rstrip("/")
    return docker_default.rstrip("/") if _running_in_container() else local_default.rstrip("/")


def _vault_url() -> str:
    return _service_url("VAULT_URL", "http://vault:8300", "http://127.0.0.1:8300")


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
    """List entities + last watermark per entity, merged for the UI.

    Served by the generic mcp-infra ``cartridge_list_entities`` tool (reads
    entity_config + entity_watermarks from Postgres) instead of proxying to a
    per-cartridge container — part of the cartridge-runtime unification. See
    docs/design/cartridge-runtime-unification.md.
    """
    _require_cartridge_visible(user, cartridge)
    from app.services import mcp_registry

    result = await mcp_registry.invoke(
        "infra", "cartridge_list_entities", {"cartridge_id": cartridge}, user=user
    )
    ent_list = result if isinstance(result, list) else (result.get("entities") or [])
    out = []
    for ent in ent_list:
        item = dict(ent)
        item["watermark"] = (
            {
                "entity": ent.get("entity"),
                "watermark_field": ent.get("watermark_field"),
                "last_watermark": ent.get("last_watermark"),
            }
            if ent.get("watermark_field")
            else None
        )
        out.append(item)
    return {"entities": out}


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


async def _reveal_vault_connection(user: dict, cartridge: str, conn_id: str) -> dict:
    """Read the full connection payload (incl. token) from the Vault service.

    The Vault scopes connections by the SIGNED security context (tenant/
    workspace), not by a prefixed conn_id — so we pass the plain conn_id plus
    the signed context, exactly like console's own reveal endpoint. Raises 404
    if not configured for this workspace."""
    headers = {
        **_vault_headers(),
        "x-security-context": json.dumps(build_security_context(user), ensure_ascii=False),
    }
    async with httpx.AsyncClient(headers=headers, timeout=8.0) as c:
        r = await c.get(
            f"{_VAULT_URL}/connections/{quote(cartridge, safe='')}/{quote(conn_id, safe='')}"
        )
    if r.status_code == 404:
        raise HTTPException(404, "connection not configured")
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, dict) else {}


def _load_connector_auth(cartridge: str) -> dict:
    """Return the auth block from the cartridge's connector.yaml."""
    import yaml

    candidates = [
        Path(f"/registry/cartridges/{cartridge}/app/config/connector.yaml"),
        Path(__file__).resolve().parents[3] / "cartridges" / cartridge / "app" / "config" / "connector.yaml",
    ]
    for path in candidates:
        if path.exists():
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            block = data.get("connector", data)
            return dict(block.get("auth") or {})
    return {}


def _build_auth_headers_from_connector(auth_cfg: dict, conn: dict) -> dict[str, str]:
    """Build outbound auth headers from connector.yaml + the revealed connection."""
    header = str(auth_cfg.get("header") or "Authorization").strip()
    prefix = str(auth_cfg.get("prefix") if auth_cfg.get("prefix") is not None else "Bearer ")
    atype = str(auth_cfg.get("type") or "bearer_token").strip().lower()
    token = next(
        (conn[k] for k in ("token", "access_token", "api_token", "api_key", "password") if conn.get(k)),
        None,
    )
    if atype in {"bearer_token", "api_key", "token", "apikey"} and token:
        return {header: f"{prefix}{token}"}
    if atype == "basic":
        import base64

        username = conn.get("username") or conn.get("user") or conn.get("admin_user")
        password = conn.get("password") or token
        if username and password:
            encoded = base64.b64encode(f"{username}:{password}".encode()).decode()
            return {"Authorization": f"Basic {encoded}"}
    return {}


@router.post(
    "/{cartridge}/test_connection",
    dependencies=[Depends(require_csrf), Depends(require_permission("cartridges.write"))],
)
async def test_connection(
    cartridge: str,
    request: Request,
    conn_id: str | None = Query(default=None, max_length=128),
):
    """Generic connection test — no per-cartridge container.

    Reveals the workspace-scoped connection from Vault, builds auth headers
    from the cartridge's connector.yaml, and pings the external base_url
    directly. The result is honest: a reachable host that accepts the
    credentials passes; a 401/403 or unreachable host fails. Response shape:
    ``{ok, message, latency_ms}``.
    """
    user = getattr(request.state, "user", None) or {}
    _require_cartridge_visible(user, cartridge)
    selected_conn_id = _normalize_conn_id(conn_id) or _DEFAULT_CONN_ID

    started = time.monotonic()
    ok = False
    message = ""
    payload: dict = {}
    try:
        conn = await _reveal_vault_connection(user, cartridge, selected_conn_id)
        base_url = str(conn.get("base_url") or conn.get("url") or conn.get("host") or "").strip()
        if not base_url:
            latency_ms = int((time.monotonic() - started) * 1000)
            message = "La conexión no tiene base_url configurada."
            payload = {"status": "missing_base_url"}
        else:
            auth_cfg = _load_connector_auth(cartridge)
            auth_headers = _build_auth_headers_from_connector(auth_cfg, conn)
            async with httpx.AsyncClient(timeout=10.0, headers={"Accept": "application/json", **auth_headers}) as c:
                r = await c.get(base_url)
            latency_ms = int((time.monotonic() - started) * 1000)
            if r.status_code in (401, 403):
                ok = False
                message = f"Credenciales rechazadas por el endpoint (HTTP {r.status_code})."
            else:
                ok = True
                message = f"Conectado ({latency_ms} ms, HTTP {r.status_code})."
            payload = {"status": "ok" if ok else "auth_failed", "http_status": r.status_code}
    except HTTPException as exc:
        latency_ms = int((time.monotonic() - started) * 1000)
        ok = False
        message = "Conexión no configurada en Vault." if exc.status_code == 404 else str(exc.detail)[:200]
    except Exception as exc:
        latency_ms = int((time.monotonic() - started) * 1000)
        ok = False
        # Truncate — error strings may surface internal endpoints/tokens/paths.
        message = f"No se pudo conectar: {str(exc)[:160]}" or "connection error"

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
