"""Security header and published-app theme helpers for the console."""

from __future__ import annotations

from fastapi import Response


# NOTE: script-src is intentionally strict on shell/auth/control-room paths.
# style-src still has a documented inline-style exception for legacy static
# <style> blocks until those styles move to external assets or hashes.
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "Permissions-Policy": (
        "camera=(), microphone=(), geolocation=(), payment=(), "
        "usb=(), magnetometer=(), gyroscope=(), accelerometer=()"
    ),
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    ),
}
VIEWER_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "Permissions-Policy": (
        "camera=(), microphone=(), geolocation=(), payment=(), "
        "usb=(), magnetometer=(), gyroscope=(), accelerometer=()"
    ),
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; "
        "connect-src 'self'; "
        "frame-ancestors 'self'; "
        "base-uri 'self'; "
        "form-action 'self'"
    ),
}
APP_EMBED_SECURITY_HEADERS = {
    **VIEWER_SECURITY_HEADERS,
    "X-Frame-Options": "SAMEORIGIN",
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; "
        "connect-src 'self'; "
        "frame-src 'self'; "
        "frame-ancestors 'self'; "
        "base-uri 'self'; "
        "form-action 'self'"
    ),
}
STRICT_AUTH_SECURITY_HEADERS = {
    **SECURITY_HEADERS,
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    ),
}
CONTROL_ROOM_SECURITY_HEADERS = {
    **SECURITY_HEADERS,
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    ),
}
STRICT_CSP_PATHS = frozenset(
    {
        "/login",
        "/me",
        "/forgot-password",
        "/reset-password",
        "/activate",
    }
)

APP_THEME_SHIM = """
<style id="omega-app-theme-shim">
:root,
:root[data-theme="light"] {
  --bg: #f5f7fa;
  --bg2: #ffffff;
  --bg3: #eef2f7;
  --border: #d8dee8;
  --text: #0f172a;
  --text1: #0f172a;
  --text2: #334155;
  --text3: #64748b;
  --green: #117a3d;
  --blue: #0a6ed1;
  --cyan: #0a6ed1;
  --amber: #b06d00;
  --red: #b3261e;
  --purple: #6d5bd0;
  --primary: #0a6ed1;
  --primary-hover: #085caf;
  --primary-soft: rgba(10, 110, 209, 0.10);
  --success-soft: rgba(17, 122, 61, 0.10);
  --warning-soft: rgba(176, 109, 0, 0.12);
  --danger-soft: rgba(179, 38, 30, 0.08);
  --info-soft: rgba(10, 110, 209, 0.10);
  --on-primary: #ffffff;
  --font-mono: 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace;
}
:root[data-theme="dark"] {
  --bg: #0f1822;
  --bg2: #182331;
  --bg3: #1f2c3d;
  --border: rgba(226, 232, 240, 0.14);
  --text: #e6edf6;
  --text1: #e6edf6;
  --text2: #c5cfdc;
  --text3: #8a96a8;
  --green: #4cb27b;
  --blue: #4ea3e0;
  --cyan: #4ea3e0;
  --amber: #d4a042;
  --red: #e0716b;
  --purple: #b8a7f5;
  --primary: #4ea3e0;
  --primary-hover: #74b8e8;
  --primary-soft: rgba(78, 163, 224, 0.16);
  --success-soft: rgba(76, 178, 123, 0.16);
  --warning-soft: rgba(212, 160, 66, 0.18);
  --danger-soft: rgba(224, 113, 107, 0.14);
  --info-soft: rgba(78, 163, 224, 0.16);
  --on-primary: #0f172a;
}
</style>
"""
APP_THEME_SCRIPT = '<script src="/static/js/theme-switch.js" defer></script>'


def is_viewer_path(path: str) -> bool:
    return path == "/viewer" or path.startswith("/viewer/")


def is_app_embed_path(path: str) -> bool:
    return path.startswith("/apps/") and path.endswith("/embed")


def is_control_room_path(path: str) -> bool:
    return path == "/control-room" or path.startswith("/control-room/")


def apply_security_headers(response: Response, path: str = "") -> Response:
    if is_app_embed_path(path):
        headers = APP_EMBED_SECURITY_HEADERS
    elif is_control_room_path(path):
        headers = CONTROL_ROOM_SECURITY_HEADERS
    elif path in STRICT_CSP_PATHS:
        headers = STRICT_AUTH_SECURITY_HEADERS
    elif is_viewer_path(path):
        headers = VIEWER_SECURITY_HEADERS
    else:
        headers = SECURITY_HEADERS
    if is_viewer_path(path):
        if "X-Frame-Options" in response.headers:
            del response.headers["X-Frame-Options"]
    for name, value in headers.items():
        response.headers.setdefault(name, value)
    return response


def inject_published_app_theme(html: str) -> str:
    patched = html
    if "omega-app-theme-shim" not in patched:
        lower = patched.lower()
        idx = lower.rfind("</head>")
        if idx >= 0:
            patched = patched[:idx] + APP_THEME_SHIM + patched[idx:]
        else:
            patched = APP_THEME_SHIM + patched
    if "/static/js/theme-switch.js" not in patched:
        lower = patched.lower()
        idx = lower.rfind("</body>")
        if idx >= 0:
            patched = patched[:idx] + APP_THEME_SCRIPT + patched[idx:]
        else:
            patched += APP_THEME_SCRIPT
    return patched
