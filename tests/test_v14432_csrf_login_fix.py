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


def test_auth_flow_module_exists():
    assert AUTH_FLOW.exists(), "console-next/src/lib/auth-flow.ts missing"


def test_auth_flow_exports_loginUser():
    src = _read(AUTH_FLOW)
    assert "export async function loginUser" in src
    assert "loginUser(email: string, password: string)" in src


def test_auth_flow_does_get_then_post():
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
    assert "readCookie" in src
    assert "csrf_token" in src
    assert 'No se pudo' in src or "No CSRF" in src or "CSRF" in src


def test_login_page_imports_loginUser():
    src = _read(LOGIN_PAGE)
    assert 'from "@/lib/auth-flow"' in src or "from '@/lib/auth-flow'" in src
    assert "loginUser" in src


def test_login_page_no_longer_posts_to_api_auth_login():
    src = _read(LOGIN_PAGE)
    code = re.sub(r"//.*?$|/\*[\s\S]*?\*/", "", src, flags=re.MULTILINE)
    assert '"/api/auth/login"' not in code
    assert "/api/auth/login" not in code
    assert "lib/auth-flow" in src


def test_api_ts_has_readCookie_helper():
    src = _read(API_TS)
    helper = _read(REPO / "console-next/src/lib/cookies.ts")
    assert "export function readCookie" in helper
    assert "export { readCookie }" in src


def test_api_ts_attaches_csrf_token_on_mutations():
    src = _read(API_TS)
    assert "export async function apiFetch" in src
    assert 'method !== "GET" && method !== "HEAD"' in src
    assert '"X-CSRF-Token"' in src
    assert '"X-Request-ID"' in src
    assert "readCookie" in src


def test_api_ts_keeps_credentials_include_for_cookie_round_trip():
    src = _read(API_TS)
    assert 'credentials: init.credentials ?? "include"' in src


def test_main_py_cors_default_excludes_3000_and_includes_8000_8001():
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
    src = _read(COMPOSE)
    block = re.search(
        r"\n  console:\n[\s\S]*?(?=\n  [a-z_-]+:\n|\Z)", src,
    )
    assert block
    body = block.group(0)
    assert "ALLOWED_ORIGINS" in body
    assert "http://localhost:3000" not in body
    assert "http://localhost:8000" in body
    assert "http://localhost:8001" in body


def test_useKpis_still_calls_api_dashboard_kpis():
    use_kpis = REPO / "console-next/src/lib/hooks/useKpis.ts"
    if not use_kpis.exists():
        return
    src = _read(use_kpis)
    assert "/api/dashboard/kpis" in src
