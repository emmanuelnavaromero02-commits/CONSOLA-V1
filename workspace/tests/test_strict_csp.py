"""Sprint v1.24 (audit B6) — workspace shell ships under script-src 'self'.

Mirrors the console/tests/test_strict_csp.py pattern from console v1.18+:
verify the response-header dispatch directly via _apply_security_headers,
and assert the static HTML + extracted JS are wired so the strict CSP
can actually load the page.

Published apps are rendered through a sandboxed wrapper: /apps/* serves
platform-owned chrome/bridge, while /apps/*/content carries user HTML
with a CSP sandbox and no network access.

Lazy-imports app.main inside the helper so this test module doesn't
pollute sys.modules for peer tests (same discipline used in
console/tests/test_strict_csp.py).
"""
from __future__ import annotations

import importlib
import os
import re
import sys
from pathlib import Path

import pytest
from fastapi.responses import JSONResponse


# Workspace's app.main reads INTERNAL_API_KEY at import time via
# get_internal_api_key(); the helper rejects keys < 32 chars or with
# obvious dev-default fragments.
os.environ.setdefault("INTERNAL_API_KEY", "x" * 64)


def _main():
    """Lazy app.main loader; isolate workspace/app from peer service imports."""
    sys.path.insert(0, str(REPO_ROOT / "workspace"))
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name, None)
    return importlib.import_module("app.main")


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_STATIC = REPO_ROOT / "workspace" / "app" / "static"


# ── CSP header dispatch ──────────────────────────────────────────────


def _csp_for(path: str) -> str:
    """Build a JSONResponse, run it through _apply_security_headers with
    the given path, and return the resulting CSP value."""
    main_module = _main()
    resp = JSONResponse({})
    main_module._apply_security_headers(resp, path)
    return resp.headers.get("content-security-policy", "")


def test_workspace_csp_no_unsafe_inline_script_on_shell_paths():
    """Shell routes (everything not under /apps/) MUST drop
    'unsafe-inline' from script-src — that's the audit B6 fix."""
    for path in ("/", "/decisions", "/api/decisions", "/healthz", "/auth/me"):
        csp = _csp_for(path)
        # Slice script-src segment so style-src 'unsafe-inline'
        # doesn't false-positive.
        script_seg = csp.split("style-src", 1)[0]
        assert "'unsafe-inline'" not in script_seg, (
            f"{path}: script-src still allows 'unsafe-inline' — {script_seg!r}"
        )
        assert "script-src 'self'" in script_seg, (
            f"{path}: missing script-src 'self' — {script_seg!r}"
        )


def test_workspace_csp_keeps_style_unsafe_inline():
    """style-src 'unsafe-inline' stays — page <style> blocks aren't in
    scope for this sprint (deferred, like console did in v1.11)."""
    csp = _csp_for("/")
    assert "style-src 'self' 'unsafe-inline'" in csp, csp


def test_workspace_apps_wrapper_allows_only_same_origin_bridge():
    """The /apps/* wrapper is platform-owned chrome around the sandboxed
    iframe. It can keep inline bridge JS, but must not load CDN code."""
    csp = _csp_for("/apps/pnl_ejecutivo")
    script_seg = csp.split("style-src", 1)[0]
    assert "'unsafe-inline'" in script_seg
    assert "frame-src 'self'" in csp, csp
    assert "cdn.jsdelivr.net" not in csp and "cdnjs.cloudflare.com" not in csp, csp


def test_workspace_apps_content_is_sandboxed_and_cannot_connect():
    """User app HTML runs with an opaque origin and no network access.
    CDN scripts/styles remain allowed so existing charts render."""
    csp = _csp_for("/apps/pnl_ejecutivo/content")
    assert "sandbox allow-scripts" in csp, csp
    assert "allow-same-origin" not in csp, csp
    assert "connect-src 'none'" in csp, csp
    assert "frame-ancestors 'self'" in csp, csp
    assert "cdn.jsdelivr.net" in csp or "cdnjs.cloudflare.com" in csp, csp


def test_workspace_csp_keeps_frame_ancestors_none():
    """Workspace shell pages aren't iframe-embedded; X-Frame-Options DENY
    + frame-ancestors 'none' must stay."""
    csp = _csp_for("/")
    assert "frame-ancestors 'none'" in csp, csp


# ── HTML wiring: workspace.html no longer ships inline JS ────────────


_INLINE_HANDLER_RE = re.compile(
    r'\bon(?:click|change|submit|input|keydown|mousedown|load|error|'
    r'scroll|mouseover|mouseout|mouseenter|mouseleave|focus|blur)\s*=\s*"',
    re.IGNORECASE,
)
_INLINE_SCRIPT_WITH_CODE_RE = re.compile(
    r"<script(?![^>]*\bsrc=)[^>]*>([^<]+?)</script>",
    re.IGNORECASE | re.DOTALL,
)


def test_workspace_html_has_no_inline_event_handlers():
    """The HTML shell must have zero `on*=` attributes — strict CSP would
    block them at runtime. The 31 pre-v1.24 onclicks were converted to
    data-action / data-* dispatched via a single document-level listener
    in workspace.js."""
    html = (WORKSPACE_STATIC / "workspace.html").read_text(encoding="utf-8")
    matches = _INLINE_HANDLER_RE.findall(html)
    assert not matches, (
        f"workspace.html still has inline on*= handlers: {matches!r}"
    )


def test_workspace_html_has_no_inline_script_with_code():
    """No `<script>...</script>` blocks with a non-empty body. Bare
    `<script src="..."></script>` is fine."""
    html = (WORKSPACE_STATIC / "workspace.html").read_text(encoding="utf-8")
    bodies = [b.strip() for b in _INLINE_SCRIPT_WITH_CODE_RE.findall(html) if b.strip()]
    assert not bodies, (
        f"workspace.html still has inline <script> with code "
        f"(first 200 chars): {bodies[0][:200]!r}"
    )


def test_workspace_html_references_extracted_js():
    """The shell must load the extracted external JS — without this the
    page is just static HTML with no behavior."""
    html = (WORKSPACE_STATIC / "workspace.html").read_text(encoding="utf-8")
    assert '/static/js/workspace.js' in html, (
        "workspace.html must reference /static/js/workspace.js (the "
        "extracted shell script)"
    )


def test_extracted_workspace_js_exists_and_has_event_listener():
    """The .js file must exist and call addEventListener — the
    delegated handler that replaces the 31 inline onclicks lives here."""
    js_path = WORKSPACE_STATIC / "js" / "workspace.js"
    assert js_path.is_file(), f"missing {js_path}"
    src = js_path.read_text(encoding="utf-8")
    assert "addEventListener" in src, (
        "workspace.js should call addEventListener — the inline on* "
        "handlers were removed and need to bind via the delegated listener"
    )
    # The dispatcher must handle each of the 18 distinct data-action
    # values used in the HTML / template literals. A future refactor
    # that drops a case silently breaks the matching button.
    expected_actions = (
        "apps-reload", "chat-reset", "chat-example", "chat-send",
        "decision-new", "decision-submit", "modal-close", "modal-backdrop",
        "logout", "kpi-add", "switch-tab", "dec-filter", "detail-tab",
        "decision-action-add", "decision-delete", "decision-save-overview",
        "decision-save-kpis", "kpi-remove",
    )
    missing = [a for a in expected_actions if f"'{a}'" not in src]
    assert not missing, (
        f"workspace.js delegated handler is missing case(s): {missing}"
    )
