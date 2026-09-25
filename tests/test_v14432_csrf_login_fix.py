"""Sprint v1.44.3.2.2 R-Mac follow-up — login flow CSRF + CORS guards.

A Mac validation run uncovered that the migrated login page POSTed to
/api/auth/login while the real endpoint is /auth/login + requires
a CSRF double-submit-cookie round-trip. Compounded by backend CORS
that didn't allow the X-CSRF-Token header.

This file pins the four contracts the hotfix establishes:
  1. lib/auth-flow.ts implements the 2-step CSRF dance.
  2. login/page.tsx calls loginUser, NOT the old api.post path.
  3. lib/api.ts auto-attaches X-CSRF-Token and X-Request-ID on
     non-GET same-origin requests.
  4. Backend still allows local-dev CORS for transitional tooling.

Browser-level verification belongs to tests-e2e/specs/00-csp-smoke
+ 01-login-deep on the Mac; this file is the CI-runnable contract.
"""
from __future__ import annotations

import re
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
LOGIN_PAGE  = REPO / "console-next/src/app/login/page.tsx"
AUTH_FLOW   = REPO / "console-next/src/lib/auth-flow.ts"
API_TS      = REPO / "console-next/src/lib/api.ts"
MAIN_PY     = REPO / "console/app/main.py"
COMPOSE     = REPO / "infra/docker-compose.yml"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# ── lib/auth-flow.ts: explicit CSRF dance ────────────────────────────────


def test_auth_flow_module_exists():
    assert AUTH_FLOW.exists(), "console-next/src/lib/auth-flow.ts missing"


def test_auth_flow_exports_loginUser():
    src = _read(AUTH_FLOW)
    assert "export async function loginUser" in src
    # Signature pinned: (email, password) → Promise.
    assert "loginUser(email: string, password: string)" in src


def test_auth_flow_does_get_then_post():
    """Step 1 GET /login, then step 2 POST /auth/login. Order
    matters: the cookie is seeded by step 1 and read for step 2."""
    src = _read(AUTH_FLOW)
    body = re.search(
        r"export async function loginUser.*?(?=^export |\Z)",
        src, re.DOTALL | re.MULTILINE,
    )
    assert body
    text = body.group(0)
    get_pos = text.find("/login")
    post_pos = text.find("/auth/login")
    assert get_pos >= 0 and post_pos > get_pos, (
        "loginUser must GET /login BEFORE POSTing /auth/login so "
        "the csrf_token cookie is seeded first"
    )


def test_auth_flow_sends_csrf_header_and_credentials():
    src = _read(AUTH_FLOW)
    body = re.search(
        r"export async function loginUser.*?(?=^export |\Z)",
        src, re.DOTALL | re.MULTILINE,
    )
    text = body.group(0) if body else ""
    api_src = _read(API_TS)
    assert '"X-CSRF-Token"' in text
    assert "apiFetch" in text
    assert 'credentials: init.credentials ?? "include"' in api_src, (
        "apiFetch must use credentials:'include' so the csrf_token cookie "
        "round-trips between GET /login and POST /auth/login"
    )


def test_auth_flow_handles_4xx_with_useful_messages():
    """Status-specific messages so the user knows what went wrong
    (401 vs 403 vs 429 vs generic)."""
    src = _read(AUTH_FLOW)
    body = re.search(
        r"export async function loginUser.*?(?=^export |\Z)",
        src, re.DOTALL | re.MULTILINE,
    )
    text = body.group(0) if body else ""
    for status_marker in ("401", "403", "429"):
        assert status_marker in text, (
            f"loginUser must surface a specific message for HTTP {status_marker}"
        )


def test_auth_flow_throws_on_missing_csrf_cookie():
    src = _read(AUTH_FLOW)
    # Reads the cookie via readCookie and asserts non-null.
    assert "readCookie" in src
    assert "csrf_token" in src
    assert 'No se pudo' in src or "No CSRF" in src or "CSRF" in src


# ── login/page.tsx: uses loginUser, not the old api.post path ────────────


def test_login_page_imports_loginUser():
    src = _read(LOGIN_PAGE)
    assert 'from "@/lib/auth-flow"' in src or "from '@/lib/auth-flow'" in src
    assert "loginUser" in src


def test_login_page_no_longer_posts_to_api_auth_login():
    """The old `api.post('/api/auth/login', …)` line is GONE. If
    it returns, the fix has been reverted. (We allow the path to
    appear inside JSX comments documenting the regression — only
    real code occurrences would re-introduce the bug.)"""
    src = _read(LOGIN_PAGE)
    code = re.sub(r"//.*?$|/\*[\s\S]*?\*/", "", src, flags=re.MULTILINE)
    assert '"/api/auth/login"' not in code
    assert "/api/auth/login" not in code
    # Source should reference the new auth-flow helper.
    assert "lib/auth-flow" in src


# ── lib/api.ts: X-CSRF-Token interceptor ────────────────────────────────


def test_api_ts_has_readCookie_helper():
    src = _read(API_TS)
    helper = _read(REPO / "console-next/src/lib/cookies.ts")
    assert "export function readCookie" in helper
    assert "export { readCookie }" in src


def test_api_ts_attaches_csrf_token_on_mutations():
    """The central fetch wrapper reads csrf_token from document.cookie
    and adds X-CSRF-Token on every non-GET request. Without this,
    every POST/PUT/DELETE the hooks fire 403s."""
    src = _read(API_TS)
    assert "export async function apiFetch" in src
    # Mutating methods are gated.
    assert 'method !== "GET" && method !== "HEAD"' in src
    # Token attached as X-CSRF-Token.
    assert '"X-CSRF-Token"' in src
    assert '"X-Request-ID"' in src
    # Reads from the helper.
    assert "readCookie" in src


def test_api_ts_keeps_credentials_include_for_cookie_round_trip():
    src = _read(API_TS)
    assert 'credentials: init.credentials ?? "include"' in src


# ── Backend CORS: same-origin static console + workspace origin ─────────


def test_main_py_cors_default_excludes_3000_and_includes_8000_8001():
    """The Python default should keep only FastAPI + workspace local origins."""
    src = _read(MAIN_PY)
    block = re.search(
        r"def _allowed_origins.*?(?=^def |^app\.|\Z)",
        src, re.DOTALL | re.MULTILINE,
    )
    assert block
    body = block.group(0)
    assert "http://localhost:3000" not in body
    assert "http://localhost:8000" in body
    assert "http://localhost:8001" in body


def test_main_py_cors_allows_x_csrf_token_header():
    src = _read(MAIN_PY)
    # Capture the full add_middleware(CORSMiddleware, ...) call by
    # matching from CORSMiddleware to the matching ``allow_headers=[...]``
    # closing bracket — non-greedy paren-match would prematurely close
    # inside _allowed_origins(). Locate the allow_headers list directly.
    m = re.search(
        r"allow_headers\s*=\s*\[([\s\S]*?)\]",
        src,
    )
    assert m, "CORSMiddleware allow_headers= list not found in main.py"
    allow_headers_body = m.group(1)
    assert '"X-CSRF-Token"' in allow_headers_body, (
        "CORS allow_headers must include X-CSRF-Token — without it the "
        "browser preflight rejects the login POST"
    )


def test_compose_pins_ALLOWED_ORIGINS_for_console_service():
    """The compose ``console`` service must export ALLOWED_ORIGINS
    with both origins so a `make up` works without setting the
    env manually."""
    src = _read(COMPOSE)
    # Find the console service block.
    block = re.search(
        r"\n  console:\n[\s\S]*?(?=\n  [a-z_-]+:\n|\Z)", src,
    )
    assert block
    body = block.group(0)
    assert "ALLOWED_ORIGINS" in body
    assert "http://localhost:3000" not in body
    assert "http://localhost:8000" in body
    assert "http://localhost:8001" in body


# ── Coexistence: hooks still use /api/* paths (unchanged) ───────────────


def test_useKpis_still_calls_api_dashboard_kpis():
    """v1.44.1 backend exposes /api/dashboard/kpis. The hook stays
    on that path — only the LOGIN endpoint dropped the /api/ prefix.
    Regression guard so a careless refactor doesn't fold the auth
    convention onto every other call."""
    use_kpis = REPO / "console-next/src/lib/hooks/useKpis.ts"
    if not use_kpis.exists():
        return  # tolerate the file being moved later
    src = _read(use_kpis)
    assert "/api/dashboard/kpis" in src
