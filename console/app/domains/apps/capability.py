from __future__ import annotations

import base64
import hmac
import json
import secrets
import time
from typing import Any

from app.services.security_context import sign_server_payload

CAPABILITY_PURPOSE = "published_app_content"
CAPABILITY_VERSION = "omega.app-content-capability.v1"
CAPABILITY_TTL_SECONDS = 120
_MAX_CLOCK_SKEW_SECONDS = 5


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64url(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _canonical(claims: dict[str, Any]) -> bytes:
    return json.dumps(
        claims, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def issue_content_capability(
    *,
    app_name: str,
    cartridge_id: str,
    tenant_id: str,
    workspace_id: str,
    user_id: Any,
    manifest_digest: str,
    now: int | None = None,
    ttl_seconds: int = CAPABILITY_TTL_SECONDS,
) -> str:
    issued_at = int(now if now is not None else time.time())
    claims = {
        "v": CAPABILITY_VERSION,
        "purpose": CAPABILITY_PURPOSE,
        "app": str(app_name),
        "cartridge": str(cartridge_id),
        "tenant": str(tenant_id),
        "workspace": str(workspace_id),
        "user": str(user_id),
        "digest": str(manifest_digest),
        "iat": issued_at,
        "exp": issued_at + int(ttl_seconds),
        "jti": secrets.token_urlsafe(12),
    }
    payload = _canonical(claims)
    signature = sign_server_payload(payload, purpose=CAPABILITY_PURPOSE)
    return f"{_b64url(payload)}.{signature}"


def _signed_claims(token: str) -> dict[str, Any] | None:
    if not token or not isinstance(token, str) or token.count(".") != 1:
        return None
    encoded, signature = token.split(".", 1)
    try:
        payload = _unb64url(encoded)
        claims = json.loads(payload.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(claims, dict):
        return None

    if len(signature) != 64 or any(
        char not in "0123456789abcdef" for char in signature
    ):
        return None
    try:
        expected = sign_server_payload(_canonical(claims), purpose=CAPABILITY_PURPOSE)
    except (RuntimeError, ValueError):
        return None
    if not hmac.compare_digest(str(signature), expected):
        return None
    return claims


def verify_content_capability_envelope(
    token: str,
    *,
    app_name: str,
    now: int | None = None,
) -> dict[str, Any] | None:
    claims = _signed_claims(token)
    if claims is None:
        return None

    if claims.get("v") != CAPABILITY_VERSION:
        return None
    if claims.get("purpose") != CAPABILITY_PURPOSE:
        return None
    if str(claims.get("app") or "") != str(app_name):
        return None
    if any(
        not str(claims.get(field) or "")
        for field in ("cartridge", "tenant", "workspace", "user", "digest")
    ):
        return None

    current = int(now if now is not None else time.time())
    try:
        issued_at = int(claims.get("iat"))
        expires_at = int(claims.get("exp"))
    except (TypeError, ValueError):
        return None
    if expires_at <= current or issued_at > current + _MAX_CLOCK_SKEW_SECONDS:
        return None
    if expires_at - issued_at > CAPABILITY_TTL_SECONDS:
        return None
    if not str(claims.get("jti") or ""):
        return None
    return claims


def verify_content_capability(
    token: str,
    *,
    app_name: str,
    tenant_id: str,
    workspace_id: str,
    user_id: Any,
    manifest_digest: str | None,
    now: int | None = None,
) -> dict[str, Any] | None:
    claims = verify_content_capability_envelope(
        token,
        app_name=app_name,
        now=now,
    )
    if claims is None:
        return None
    if str(claims.get("tenant") or "") != str(tenant_id or ""):
        return None
    if str(claims.get("workspace") or "") != str(workspace_id or ""):
        return None
    if str(claims.get("user") or "") != str(user_id):
        return None
    if not manifest_digest or str(claims.get("digest") or "") != str(manifest_digest):
        return None
    return claims


_ALLOWED_DEST = frozenset({"iframe", "frame"})
_ALLOWED_MODE = frozenset({"navigate"})
_ALLOWED_SITE = frozenset({"same-origin", "same-site"})


def frame_fetch_metadata_ok(headers: Any) -> bool:
    dest = str(headers.get("sec-fetch-dest") or "").lower()
    mode = str(headers.get("sec-fetch-mode") or "").lower()
    site = str(headers.get("sec-fetch-site") or "").lower()
    if not dest or not mode or not site:
        return False
    return dest in _ALLOWED_DEST and mode in _ALLOWED_MODE and site in _ALLOWED_SITE


def api_navigation_blocked(headers: Any) -> bool:
    dest = str(headers.get("sec-fetch-dest") or "").lower()
    if dest in {"document", "iframe", "frame", "embed", "object"}:
        return True
    mode = str(headers.get("sec-fetch-mode") or "").lower()
    site = str(headers.get("sec-fetch-site") or "").lower()
    if mode == "navigate" and site not in {"same-origin"}:
        return True
    return False
