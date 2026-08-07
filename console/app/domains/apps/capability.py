"""Short-lived capability that admits one app frame to ``/content``.

``/apps/{name}/content`` used to be an ordinary session-authenticated page. As
a page it is fine inside the wrapper, but a sandboxed app could open it — or be
opened — as a top-level document, and from there navigate to an authenticated
API: the browser re-attaches a ``SameSite=Lax`` cookie on a top-level GET, so
the navigation lands as a credentialed request.

So the session cookie stops being what authorises this route. ``/embed`` mints a
capability bound to exactly one app, scope, user and manifest revision, valid
for seconds, and the wrapper puts it on the inner frame's URL. The capability
authorises *rendering this app's HTML in a frame* — nothing else. It grants no
dataset, no API, no other app, no other workspace.

Signed with ``sign_server_payload`` under its own purpose, so the key is
derived per purpose and a capability cannot be replayed as any other signed
artefact. No new cryptography, and nothing here is a bearer secret the app can
reuse: it is scoped, expiring and validated server side on every request.
"""

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
# Long enough for the frame to load and reload during a normal session, short
# enough that a leaked URL is worthless. Reloads inside the frame keep working
# until it lapses; after that the wrapper re-mints on its own next load.
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
    """Return the claims when the capability admits exactly this request.

    Every binding is re-checked against what the server independently knows —
    the app in the path, the caller's resolved scope, the digest of what is
    about to be served. A capability for another app, another workspace,
    another user or an older revision fails here, which is what stops a leaked
    URL from being useful anywhere but where it was minted.
    """
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

    # Constant-time, and over the exact bytes that were signed.
    try:
        expected = sign_server_payload(_canonical(claims), purpose=CAPABILITY_PURPOSE)
    except (RuntimeError, ValueError):
        return None
    if not hmac.compare_digest(str(signature), expected):
        return None

    if claims.get("v") != CAPABILITY_VERSION:
        return None
    if claims.get("purpose") != CAPABILITY_PURPOSE:
        return None
    if str(claims.get("app") or "") != str(app_name):
        return None
    if str(claims.get("tenant") or "") != str(tenant_id or ""):
        return None
    if str(claims.get("workspace") or "") != str(workspace_id or ""):
        return None
    if str(claims.get("user") or "") != str(user_id):
        return None
    if not manifest_digest or str(claims.get("digest") or "") != str(manifest_digest):
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


# --- Fetch Metadata -------------------------------------------------------

# Secondary, never sole: the capability above is the control. These headers add
# a cheap check that the request really is a frame load from our own origin,
# which is what makes a top-level navigation to /content fail even if someone
# somehow holds a live capability.
_ALLOWED_DEST = frozenset({"iframe", "frame"})
_ALLOWED_MODE = frozenset({"navigate"})
_ALLOWED_SITE = frozenset({"same-origin", "same-site"})


def frame_fetch_metadata_ok(headers: Any) -> bool:
    dest = str(headers.get("sec-fetch-dest") or "").lower()
    mode = str(headers.get("sec-fetch-mode") or "").lower()
    site = str(headers.get("sec-fetch-site") or "").lower()
    if not dest or not mode or not site:
        # Absent headers fail closed: every browser that can run a published
        # app sends them, so a request without them is not a frame load we
        # are willing to serve untrusted HTML to.
        return False
    return dest in _ALLOWED_DEST and mode in _ALLOWED_MODE and site in _ALLOWED_SITE


def api_navigation_blocked(headers: Any) -> bool:
    """True when an /api request is really a document navigation.

    A sandboxed app cannot fetch an authenticated endpoint — ``connect-src
    'none'`` sees to that — but it can still try to *navigate* to one, and a
    top-level GET carries the Lax session cookie. Nothing under /api is meant
    to be reached that way, so a document/frame destination is refused before
    any handler runs.
    """
    dest = str(headers.get("sec-fetch-dest") or "").lower()
    if dest in {"document", "iframe", "frame", "embed", "object"}:
        return True
    mode = str(headers.get("sec-fetch-mode") or "").lower()
    site = str(headers.get("sec-fetch-site") or "").lower()
    # A navigation from an opaque origin reports "cross-site"; a same-origin
    # XHR/fetch never reports mode=navigate.
    if mode == "navigate" and site not in {"same-origin"}:
        return True
    return False
