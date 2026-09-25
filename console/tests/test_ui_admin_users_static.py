from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT       = Path(__file__).resolve().parents[2]
ADMIN_HTML      = REPO_ROOT / "console/app/static/admin_users.html"
ADMIN_JS        = REPO_ROOT / "console/app/static/js/admin_users.js"
VAULT_HTML      = REPO_ROOT / "console/app/static/viewers/vault.html"
VAULT_JS        = REPO_ROOT / "console/app/static/js/viewers/vault.js"
STUDIO_HTML     = REPO_ROOT / "console/app/static/studio.html"
TOKENS_CSS      = REPO_ROOT / "console/app/static/css/tokens.css"
MAIN_PY         = REPO_ROOT / "console/app/main.py"
ROUTERS_V1      = REPO_ROOT / "console/app/routers/v1"


INLINE_HANDLER_RE = re.compile(
    r"\s+on(click|change|input|submit|load|keydown|keyup|mouseover|mouseout|focus|blur)\s*=",
    re.IGNORECASE,
)


def test_admin_html_has_no_inline_event_handlers():
    html = ADMIN_HTML.read_text(encoding="utf-8")
    matches = INLINE_HANDLER_RE.findall(html)
    assert not matches, (
        f"admin_users.html still has inline event handlers: {matches}. "
        f"Wire them with addEventListener in admin_users.js."
    )


def test_admin_html_loads_external_admin_users_js():
    html = ADMIN_HTML.read_text(encoding="utf-8")
    assert "/static/js/admin_users.js" in html, (
        "admin_users.html must load /static/js/admin_users.js"
    )


def test_admin_html_loads_tokens_css():
    html = ADMIN_HTML.read_text(encoding="utf-8")
    pos_tokens = html.find("tokens.css")
    pos_main   = html.find("main.css")
    assert pos_tokens != -1, "admin_users.html must <link> tokens.css"
    assert pos_main   != -1, "admin_users.html must still load main.css"
    assert pos_tokens < pos_main, (
        "tokens.css must be linked before main.css so cascade wins"
    )


def test_admin_html_no_legacy_global_function_calls():
    html = ADMIN_HTML.read_text(encoding="utf-8")
    dead = [
        "openInviteModal(", "editUser(", "closeModal(",
        "submitInvite(", "saveUser(", "reinvite(", "sendReset(",
        "deleteUser(", "doLogout(",
    ]
    leftover = [c for c in dead if c in html]
    assert not leftover, f"Dead legacy callsites in admin_users.html: {leftover}"


def test_admin_html_no_json_in_html_attribute():
    html = ADMIN_HTML.read_text(encoding="utf-8")
    assert "JSON.stringify(" not in html, (
        "admin_users.html must not embed JSON.stringify(...) in attributes; "
        "resolve row records from a JS-side Map keyed by id."
    )


def test_admin_html_modals_are_static():
    html = ADMIN_HTML.read_text(encoding="utf-8")
    for mid in ("invite-modal", "edit-modal", "confirm-modal"):
        assert f'id="{mid}"' in html, f"admin_users.html must declare static <div id=\"{mid}\">"


def test_admin_js_exists_and_is_module_scoped():
    js = ADMIN_JS.read_text(encoding="utf-8")
    assert js.strip(), "admin_users.js must not be empty"
    assert "window." not in js, (
        "admin_users.js must not attach controllers to window.*; "
        "module scope + addEventListener is enough."
    )


def test_admin_js_handles_401_and_403_distinctly():
    js = ADMIN_JS.read_text(encoding="utf-8")
    assert "status === 401" in js, (
        "admin_users.js must branch on HTTP 401 (session expired)"
    )
    assert "status === 403" in js, (
        "admin_users.js must branch on HTTP 403 (missing permission)"
    )
    assert "showPermissionDenied" in js, (
        "admin_users.js must surface a permission-denied banner, not a generic message"
    )


def test_admin_js_has_loading_error_success_states():
    js = ADMIN_JS.read_text(encoding="utf-8")
    for primitive in ("withLoading", "setModalError", "toast("):
        assert primitive in js, (
            f"admin_users.js must implement {primitive!r} for "
            f"loading/error/success feedback"
        )
    html = ADMIN_HTML.read_text(encoding="utf-8")
    assert "Cargando" in html or "Cargando" in js, "Initial loading copy missing"


def test_admin_js_uses_no_blocking_native_dialogs():
    js = ADMIN_JS.read_text(encoding="utf-8")
    stripped = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    stripped = re.sub(r"//[^\n]*", "", stripped)
    assert "alert(" not in stripped, "admin_users.js must not use alert()"
    assert "confirm(" not in stripped, "admin_users.js must not use confirm()"


def test_admin_js_renderers_use_safe_dom_apis():
    js = ADMIN_JS.read_text(encoding="utf-8")
    body = _function_body(js, "buildRow")
    assert "createElement(" in body and "textContent" in body, (
        "buildRow must use createElement + textContent (not innerHTML with templates)"
    )
    assert ".innerHTML =" not in body, (
        "buildRow must not assign innerHTML — it bypasses escaping"
    )


def test_admin_js_only_calls_existing_backend_endpoints():
    js = ADMIN_JS.read_text(encoding="utf-8")
    py = _console_route_source()

    pattern = re.compile(r"/(?:api/admin/users|auth/[a-z\-]+)[^\s`'\"\\]*")
    js_urls = {_normalize(u) for u in pattern.findall(js)}
    assert js_urls, "admin_users.js should call /api/admin/users/* or /auth/*"

    route_re = re.compile(
        r'@app\.(?:get|post|put|patch|delete)\(\s*"(/(?:api/admin/users|auth)[^"]*)"',
        re.S,
    )
    py_routes = {_normalize(m) for m in route_re.findall(py)}

    missing = js_urls - py_routes
    assert not missing, (
        f"admin_users.js calls endpoints not declared in console routes: {missing}. "
        f"Known: {sorted(py_routes)}"
    )


def test_admin_js_gates_mutating_actions_by_role():
    js = ADMIN_JS.read_text(encoding="utf-8")
    assert "canWrite" in js, (
        "admin_users.js must expose a canWrite flag derived from /auth/me role"
    )
    assert "btn-invite" in js and ".hidden" in js, (
        "admin_users.js must hide #btn-invite for non-admins"
    )
    body = _function_body(js, "buildRow")
    assert "canWrite" in body, "buildRow must condition action buttons on canWrite"


def test_admin_js_invite_validates_email_format():
    js = ADMIN_JS.read_text(encoding="utf-8")
    assert "isValidEmail" in js, (
        "admin_users.js must validate the invitation email format before POST"
    )
    body = _function_body(js, "submitInvite")
    assert "isValidEmail" in body, "submitInvite must call isValidEmail"


def test_phase_a_does_not_touch_vault_or_studio():
    vault_html = VAULT_HTML.read_text(encoding="utf-8")
    vault_js   = VAULT_JS.read_text(encoding="utf-8")
    assert "/static/js/viewers/vault.js" in vault_html
    assert "function escHtml" in vault_js

    assert STUDIO_HTML.is_file(), "studio.html must not be deleted by this phase"


def test_tokens_css_used_by_admin_users():
    css = TOKENS_CSS.read_text(encoding="utf-8")
    for tok in ("--modal-bg", "--danger", "--primary-soft", "--bg3"):
        assert tok in css, f"tokens.css missing {tok} used by admin_users.html"


TEMPLATE_RE  = re.compile(r"\$\{[^}]*\}")
PATHPARAM_RE = re.compile(r"\{[A-Za-z_][A-Za-z0-9_]*\}")


def _normalize(url: str) -> str:
    out = TEMPLATE_RE.sub("{}", url)
    out = PATHPARAM_RE.sub("{}", out)
    return out.rstrip("/")


def _function_body(src: str, name: str) -> str:
    m = re.search(rf"function\s+{name}\s*\([^)]*\)\s*\{{", src)
    assert m, f"{name} not found"
    depth = 1
    i = m.end()
    while i < len(src) and depth:
        if src[i] == "{": depth += 1
        elif src[i] == "}": depth -= 1
        i += 1
    return src[m.end():i]


def _console_route_source() -> str:
    parts = [MAIN_PY.read_text(encoding="utf-8")]
    parts.extend(
        path.read_text(encoding="utf-8").replace("@router.", "@app.")
        for path in sorted(ROUTERS_V1.glob("*.py"))
        if path.name != "__init__.py"
    )
    return "\n".join(parts)
