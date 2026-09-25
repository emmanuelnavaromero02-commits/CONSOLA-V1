from __future__ import annotations

from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
NEXT_ROOT = REPO / "console-next"
API_TS = NEXT_ROOT / "src/lib/api.ts"
AUTH_FLOW = NEXT_ROOT / "src/lib/auth-flow.ts"
PAGES_PY = REPO / "console/app/routers/pages.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_next_proxy_files_are_removed_for_static_export():
    removed = [
        NEXT_ROOT / "src/lib/proxy.ts",
        NEXT_ROOT / "src/proxy.ts",
        NEXT_ROOT / "src/app/api/[...path]/route.ts",
        NEXT_ROOT / "src/app/auth/[...path]/route.ts",
        NEXT_ROOT / "src/app/login-proxy/route.ts",
        NEXT_ROOT / "src/app/security/[...path]/route.ts",
    ]
    for path in removed:
        assert (
            not path.exists()
        ), f"{path.relative_to(REPO)} must not exist in static export mode"


def test_next_config_uses_static_export_and_fastapi_asset_prefix():
    src = _read(NEXT_ROOT / "next.config.mjs")
    assert 'output: "export"' in src
    assert 'assetPrefix: "/static/console-next"' in src
    assert "rewrites(" not in src
    assert "headers(" not in src


def test_api_client_is_relative_fetch_with_credentials_csrf_and_request_id():
    src = _read(API_TS)
    assert "NEXT_PUBLIC_API_BASE" not in src
    assert "NEXT_PUBLIC_BACKEND_URL" not in src
    assert "axios" not in src
    assert "export async function apiFetch" in src
    assert "fetch(path" in src
    assert 'credentials: init.credentials ?? "include"' in src
    assert '"X-Request-ID"' in src
    assert '"X-CSRF-Token"' in src
    assert 'readCookie("csrf_token")' in src


def test_auth_flow_uses_fastapi_paths_without_login_proxy():
    src = _read(AUTH_FLOW)
    assert 'apiFetch("/login"' in src
    assert 'apiFetch("/auth/login"' in src
    assert "/login-proxy" not in src
    assert "NEXT_PUBLIC_API_BASE" not in src
    assert "NEXT_PUBLIC_BACKEND_URL" not in src


def test_package_copy_script_does_not_publish_generated_studio_route():
    pkg = _read(NEXT_ROOT / "package.json")
    assert '"export:copy"' in pkg
    assert "cp -R out ../console/app/static/console-next" in pkg
    assert "rm -rf ../console/app/static/console-next/studio" in pkg


def test_fastapi_serves_console_next_pages_with_csrf_and_hashed_csp():
    src = _read(PAGES_PY)
    for route in (
        '"/cartridges"',
        '"/cartridges/viewer"',
        '"/copilot"',
        '"/monitor"',
        '"/viewer"',
        '"/operations/companies"',
        '"/operations/users"',
        '"/operations/audit"',
    ):
        assert route in src
    assert "_console_next_response" in src
    assert "_console_next_csp" in src
    assert "hashlib.sha256" in src
    assert "'sha256-" in src
    assert "set_csrf_cookie" in src
    assert "CONSOLE_NEXT_STATIC" in src
