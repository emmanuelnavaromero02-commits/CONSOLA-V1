from __future__ import annotations

import re

from fastapi import Response

from app.domains.security.request_classification import (
    is_app_content_capability_path,
)


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

APP_FONT_PATH_PREFIX = "/static/fonts/"
APP_THEME_FONT_DIR = "/static/fonts/inter-4.1"
# The app frame is sandboxed without allow-same-origin, so its origin is opaque
# ("null") and the browser fetches @font-face files in CORS mode without
# credentials. Only these public font files get a wildcard ACAO.
APP_FONT_CORS_HEADERS = {"Access-Control-Allow-Origin": "*"}
APP_FONT_OK_HEADERS = {
    "Content-Type": "font/woff2",
    "Cache-Control": "public, max-age=31536000, immutable",
}

# Injected after the app's own styles (before its last </head>): raw tokens per
# theme, legacy aliases at 0,2,0 so they beat the apps' :root[data-theme]
# rules, the body typeface, and zero-specificity base rules in a cascade layer
# that any unlayered app CSS overrides.
APP_THEME_SHIM = """
<style id="omega-app-theme-shim">
@font-face {
  font-family: "Inter";
  font-style: normal;
  font-weight: 100 900;
  font-display: swap;
  src: url("/static/fonts/inter-4.1/InterVariable.woff2") format("woff2");
}
@font-face {
  font-family: "Inter";
  font-style: italic;
  font-weight: 100 900;
  font-display: swap;
  src: url("/static/fonts/inter-4.1/InterVariable-Italic.woff2") format("woff2");
}
:root,
:root[data-theme="light"] {
  color-scheme: light;
  --font-sans: "Inter", ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, Arial, sans-serif;
  --font-mono: 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace;
  --radius: 8px;
  --radius-lg: 12px;
  --bg: #f8fafc;
  --card: #ffffff;
  --bg3: #f1f5f9;
  --border: rgba(15, 23, 42, 0.10);
  --border-strong: rgba(15, 23, 42, 0.18);
  --text-primary: #0f172a;
  --text-secondary: #334155;
  --text-muted: #64748b;
  --primary: #4f46e5;
  --primary-hover: #4338ca;
  --primary-soft: rgba(79, 70, 229, 0.10);
  --primary-strong: #4f46e5;
  --primary-strong-hover: #4338ca;
  --on-primary: #ffffff;
  --row-hover: rgba(15, 23, 42, 0.035);
  --shadow-card: 0 1px 2px rgba(15, 23, 42, 0.05), 0 1px 3px rgba(15, 23, 42, 0.08);
  --scrollbar-thumb: rgba(148, 163, 184, 0.55);
  --green: #117a3d;
  --blue: #0a6ed1;
  --cyan: #0a6ed1;
  --amber: #b06d00;
  --red: #b3261e;
  --purple: #6d5bd0;
  --success-soft: rgba(17, 122, 61, 0.10);
  --warning-soft: rgba(176, 109, 0, 0.12);
  --danger-soft: rgba(179, 38, 30, 0.08);
  --info-soft: rgba(10, 110, 209, 0.10);
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --bg: #090d16;
  --card: #111827;
  --bg3: #1f2937;
  --border: rgba(255, 255, 255, 0.08);
  --border-strong: rgba(255, 255, 255, 0.16);
  --text-primary: #f8fafc;
  --text-secondary: #cbd5e1;
  --text-muted: #94a3b8;
  --primary: #6366f1;
  --primary-hover: #818cf8;
  --primary-soft: rgba(99, 102, 241, 0.18);
  --primary-strong: #4f46e5;
  --primary-strong-hover: #4338ca;
  --on-primary: #ffffff;
  --row-hover: rgba(255, 255, 255, 0.04);
  --shadow-card: 0 1px 2px rgba(0, 0, 0, 0.45);
  --green: #4cb27b;
  --blue: #4ea3e0;
  --cyan: #4ea3e0;
  --amber: #d4a042;
  --red: #e0716b;
  --purple: #b8a7f5;
  --success-soft: rgba(76, 178, 123, 0.16);
  --warning-soft: rgba(212, 160, 66, 0.18);
  --danger-soft: rgba(224, 113, 107, 0.14);
  --info-soft: rgba(78, 163, 224, 0.16);
}
:root,
:root[data-theme] {
  --bg2: var(--card);
  --text: var(--text-primary);
  --text1: var(--text-primary);
  --text2: var(--text-secondary);
  --text3: var(--text-muted);
}
body {
  font-family: var(--font-sans);
}
@layer omega-base {
  :where(html) {
    scrollbar-color: var(--scrollbar-thumb) transparent;
    accent-color: var(--primary);
    -webkit-text-size-adjust: 100%;
  }
  :where(body) {
    -webkit-font-smoothing: antialiased;
    -moz-osx-font-smoothing: grayscale;
    font-feature-settings: "liga" 1, "calt" 1;
  }
  :where(button, input, select, textarea) {
    font-family: inherit;
  }
  :where(button, select, summary, [role="button"]) {
    cursor: pointer;
  }
  :where(:disabled) {
    cursor: not-allowed;
  }
  :where(:focus-visible) {
    outline: 2px solid var(--primary);
    outline-offset: 2px;
  }
  :where(table) {
    font-variant-numeric: tabular-nums;
  }
  ::selection {
    background: var(--primary-soft);
  }
  :where(.omega-card) {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: var(--radius-lg);
    box-shadow: var(--shadow-card);
    padding: 16px 20px;
  }
  :where(.omega-table) {
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
  }
  :where(.omega-table th) {
    text-align: start;
    font-weight: 600;
    font-size: 12px;
    color: var(--text2);
    background: var(--bg3);
    padding: 10px 12px;
    border-bottom: 1px solid var(--border);
    white-space: nowrap;
  }
  :where(.omega-table td) {
    padding: 10px 12px;
    border-bottom: 1px solid var(--border);
  }
  :where(.omega-table tbody tr:hover) {
    background: var(--row-hover);
  }
  :where(.omega-btn) {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 6px;
    min-height: 34px;
    padding: 0 14px;
    border-radius: var(--radius);
    border: 1px solid transparent;
    background: var(--primary-strong);
    color: var(--on-primary);
    font: inherit;
    font-weight: 500;
    line-height: 1;
    transition: background-color .15s ease, border-color .15s ease, color .15s ease;
  }
  :where(.omega-btn:hover) {
    background: var(--primary-strong-hover);
  }
  :where(.omega-btn-secondary) {
    background: transparent;
    color: var(--text-primary);
    border-color: var(--border-strong);
  }
  :where(.omega-btn-secondary:hover) {
    background: var(--row-hover);
  }
  @media (prefers-reduced-motion: reduce) {
    :where(.omega-btn) {
      transition: none;
    }
  }
}
</style>
"""
APP_THEME_SCRIPT = '<script src="/static/js/theme-switch.js" defer></script>'
_LEADING_DOCTYPE_RE = re.compile(r"\s*<!doctype[^>]*>", re.IGNORECASE)


def is_viewer_path(path: str) -> bool:
    return path == "/viewer" or path.startswith("/viewer/")


def is_app_embed_path(path: str) -> bool:
    return path.startswith("/apps/") and path.endswith("/embed")


def is_app_frame_path(path: str) -> bool:
    return is_app_embed_path(path) or is_app_content_capability_path(path)


def is_control_room_path(path: str) -> bool:
    return path == "/control-room" or path.startswith("/control-room/")


def is_app_font_path(path: str) -> bool:
    return (
        path.startswith(APP_FONT_PATH_PREFIX)
        and path.endswith(".woff2")
        and ".." not in path
    )


def apply_security_headers(response: Response, path: str = "") -> Response:
    if is_app_frame_path(path):
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
    if is_app_font_path(path):
        response.headers.update(APP_FONT_CORS_HEADERS)
        if response.status_code == 200:
            response.headers.update(APP_FONT_OK_HEADERS)
    return response


def inject_published_app_theme(html: str) -> str:
    patched = html
    if "omega-app-theme-shim" not in patched:
        lower = patched.lower()
        idx = lower.rfind("</head>")
        if idx < 0:
            doctype = _LEADING_DOCTYPE_RE.match(patched)
            idx = doctype.end() if doctype else 0
        patched = patched[:idx] + APP_THEME_SHIM + patched[idx:]
    if "/static/js/theme-switch.js" not in patched:
        lower = patched.lower()
        idx = lower.rfind("</body>")
        if idx >= 0:
            patched = patched[:idx] + APP_THEME_SCRIPT + patched[idx:]
        else:
            patched += APP_THEME_SCRIPT
    return patched
