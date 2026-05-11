"""
Static guarantees for the /viewer/vault page (Phase 3 refactor).

These tests do not exercise the running app — they read the shipped HTML/CSS
and assert structural invariants we want to keep when future PRs migrate the
remaining pages off inline handlers. Each assertion exists because the
corresponding regression was observed in the legacy console: dark mode broken
because tokens were missing, buttons calling functions that don't exist, or
inline ``onclick`` attributes preventing a strict CSP rollout.
"""
from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT      = Path(__file__).resolve().parents[2]
VAULT_HTML     = REPO_ROOT / "console/app/static/viewers/vault.html"
VAULT_JS       = REPO_ROOT / "console/app/static/js/viewers/vault.js"
TOKENS_CSS     = REPO_ROOT / "console/app/static/css/tokens.css"
MAIN_CSS       = REPO_ROOT / "console/app/static/css/main.css"
VIEWER_CSS     = REPO_ROOT / "console/app/static/css/viewer.css"
MAIN_PY        = REPO_ROOT / "console/app/main.py"


# ── HTML invariants ─────────────────────────────────────────────────────────

INLINE_HANDLER_RE = re.compile(
    r"\s+on(click|change|input|submit|load|keydown|keyup|mouseover|mouseout|focus|blur)\s*=",
    re.IGNORECASE,
)


def test_vault_html_has_no_inline_event_handlers():
    """A strict CSP without 'unsafe-inline' must not break this page."""
    html = VAULT_HTML.read_text(encoding="utf-8")
    matches = INLINE_HANDLER_RE.findall(html)
    assert not matches, (
        f"viewers/vault.html still has inline handlers: {matches}. "
        f"Wire them with addEventListener in vault.js instead."
    )


def test_vault_html_loads_external_vault_js():
    html = VAULT_HTML.read_text(encoding="utf-8")
    assert "/static/js/viewers/vault.js" in html, (
        "vault.html must load /static/js/viewers/vault.js"
    )


def test_vault_html_loads_tokens_css_before_main():
    """tokens.css must be loaded so dark-mode swaps reach legacy --bg2/--cyan."""
    html = VAULT_HTML.read_text(encoding="utf-8")
    pos_tokens = html.find("tokens.css")
    pos_main   = html.find("main.css")
    pos_viewer = html.find("viewer.css")
    assert pos_tokens != -1, "vault.html must <link> tokens.css"
    assert pos_main   != -1, "vault.html must still load main.css"
    assert pos_viewer != -1, "vault.html must still load viewer.css"
    assert pos_tokens < pos_main < pos_viewer, (
        "Load order must be tokens.css → main.css → viewer.css so cascade wins"
    )


def test_vault_html_no_legacy_function_callsites():
    """The legacy inline script declared globals (load, switchTab, openAddConn,
    saveConn, …). The new module-scoped script never exposes them, so any
    remaining string like 'switchTab(' in HTML would be a dead reference."""
    html = VAULT_HTML.read_text(encoding="utf-8")
    legacy_calls = [
        "switchTab(", "onCartridgeChange(", "openAddConn(", "openEditConn(",
        "closeConnModal(", "saveConn(", "confirmDeleteConn(", "deleteConn(",
        "openAddSecret(", "openEditSecret(", "closeSecretModal(", "saveSecret(",
        "confirmDeleteSecret(", "deleteSecret(", "revealToken(", "hideToken(",
        "revealSecret(", "hideSecret(", "closeDelModal(", "load()",
    ]
    leftover = [c for c in legacy_calls if c in html]
    assert not leftover, (
        f"Dead legacy function references in vault.html: {leftover}"
    )


# ── JS invariants ───────────────────────────────────────────────────────────

VAULT_ENDPOINT_RE = re.compile(r"/api/vault/[^\s`'\"\\]+")
TEMPLATE_RE       = re.compile(r"\$\{[^}]*\}")
PATHPARAM_RE      = re.compile(r"\{[A-Za-z_][A-Za-z0-9_]*\}")


def _normalize_js_url(url: str) -> str:
    """Collapse both JS template params (``${encodeURIComponent(x)}``) and
    FastAPI path params (``{cartridge}``) to ``{}`` so a URL written in
    either dialect compares equal."""
    out = TEMPLATE_RE.sub("{}", url)
    out = PATHPARAM_RE.sub("{}", out)
    return out.rstrip("/")


def test_vault_js_only_calls_existing_backend_endpoints():
    js = VAULT_JS.read_text(encoding="utf-8")
    py = MAIN_PY.read_text(encoding="utf-8")

    # Extract all /api/vault/... fetches from vault.js.
    js_urls = {_normalize_js_url(u) for u in VAULT_ENDPOINT_RE.findall(js)}
    assert js_urls, "vault.js must call /api/vault/* endpoints"

    # Build a set of backend routes as `/api/vault/...` with {param} placeholders.
    route_re = re.compile(r'@app\.(?:get|post|put|delete|patch)\("(/api/vault/[^"]+)"')
    py_routes = {_normalize_js_url(m) for m in route_re.findall(py)}

    missing = js_urls - py_routes
    assert not missing, (
        f"vault.js calls endpoints not declared in console/app/main.py: {missing}. "
        f"Known backend vault routes: {sorted(py_routes)}"
    )


def test_vault_js_has_no_alert_dialogs_in_happy_path():
    """We replaced alert() with toast() for normal feedback. alert() is a
    poor enterprise UX; only legitimate fallbacks should remain. This test
    pins that we don't reintroduce raw alert() casually."""
    js = VAULT_JS.read_text(encoding="utf-8")
    assert "alert(" not in js, (
        "vault.js must not use alert(); use toast() / showErr() / "
        "showPermissionDenied() instead."
    )


def test_vault_js_handles_403_explicitly():
    js = VAULT_JS.read_text(encoding="utf-8")
    assert "status === 403" in js, (
        "vault.js must branch on HTTP 403 to surface a permission-denied state"
    )


def test_vault_js_escapes_dynamic_html():
    """Every row template runs through escHtml(). A regression that builds
    a <tr> via plain template literals without escHtml would silently
    re-introduce stored XSS on conn_id / scope / key."""
    js = VAULT_JS.read_text(encoding="utf-8")
    assert "function escHtml" in js, "vault.js must define escHtml()"
    # Every renderXxxRow function must call escHtml on the user-controlled id.
    for fn in ("renderConnRow", "renderSecretRow"):
        body = _function_body(js, fn)
        assert "escHtml(" in body, f"{fn} must escape dynamic values"


def _function_body(src: str, name: str) -> str:
    m = re.search(rf"function\s+{name}\s*\([^)]*\)\s*\{{", src)
    assert m, f"{name} not found in vault.js"
    depth = 1
    i = m.end()
    while i < len(src) and depth:
        if src[i] == "{": depth += 1
        elif src[i] == "}": depth -= 1
        i += 1
    return src[m.end():i]


# ── CSS tokens invariants ───────────────────────────────────────────────────

# Color/surface tokens that MUST flip in dark mode. Typography and geometry
# tokens (font-ui, font-mono, radius-sm) are intentionally identical across
# themes so we don't list them here.
LEGACY_VIEWER_TOKENS = [
    "--bg", "--bg2", "--bg3",
    "--text", "--text2", "--text3",
    "--cyan", "--amber", "--red", "--green",
    "--border",
    "--surface", "--input-bg", "--modal-bg", "--backdrop",
]


def test_tokens_css_defines_every_legacy_viewer_token_in_light_and_dark():
    css = TOKENS_CSS.read_text(encoding="utf-8")
    # Split light root vs dark root by the explicit `[data-theme="dark"]`
    # block — both branches must define every legacy var so the old viewer
    # CSS keeps a value after theme switch.
    dark_match = re.search(
        r':root\[data-theme="dark"\]\s*\{(.+?)^\}', css, flags=re.S | re.M
    )
    assert dark_match, "tokens.css must declare :root[data-theme=\"dark\"] block"
    dark_block = dark_match.group(1)

    light_match = re.search(r":root\s*\{(.+?)^\}", css, flags=re.S | re.M)
    assert light_match, "tokens.css must declare :root block (light)"
    light_block = light_match.group(1)

    missing_light = [t for t in LEGACY_VIEWER_TOKENS if t not in light_block]
    missing_dark  = [t for t in LEGACY_VIEWER_TOKENS if t not in dark_block]
    assert not missing_light, f"tokens.css missing light tokens: {missing_light}"
    assert not missing_dark,  f"tokens.css missing dark tokens: {missing_dark}"


def test_main_and_viewer_css_import_tokens():
    main_css = MAIN_CSS.read_text(encoding="utf-8")
    viewer_css = VIEWER_CSS.read_text(encoding="utf-8")
    assert "tokens.css" in main_css, (
        "main.css must @import tokens.css so legacy --color-* tokens swap in dark mode"
    )
    assert "tokens.css" in viewer_css, (
        "viewer.css must @import tokens.css so legacy --bg2 / --cyan / --text3 swap in dark mode"
    )


def test_no_hardcoded_pure_black_text_in_vault_inline_css():
    """Defensive check: the original viewer style sheet shipped #000 text
    that did not flip in dark mode. Prevent regressions inside the inline
    <style> block of vault.html.

    #fff is allowed because it is intentional on primary buttons whose
    background is the brand accent in both themes."""
    html = VAULT_HTML.read_text(encoding="utf-8")
    style_blocks = re.findall(r"<style[^>]*>(.*?)</style>", html, flags=re.S)
    joined = "\n".join(style_blocks).lower().replace(" ", "")
    assert "color:#000" not in joined, (
        "vault.html must not hardcode #000 text in its <style> block — use --text."
    )
    assert "background:#fff" not in joined and "background-color:#fff" not in joined, (
        "vault.html must not hardcode #fff backgrounds — use --surface so dark mode flips."
    )
