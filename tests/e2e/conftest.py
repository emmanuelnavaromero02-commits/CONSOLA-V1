from __future__ import annotations

import os

import httpx
import pytest


CONSOLE = os.environ.get("E2E_CONSOLE_URL", "http://localhost:8000")
ADMIN_EMAIL = os.environ.get("E2E_ADMIN_EMAIL", "admin@example.com")
ADMIN_PASSWORD = os.environ.get("E2E_ADMIN_PASSWORD", "")

REQUIRE_STACK = os.environ.get("E2E_REQUIRE_STACK", "").strip() == "1"
RUNNING_IN_CI = os.environ.get("CI", "").strip().lower() in {"true", "1", "yes"}


def _bail(message: str) -> None:
    if REQUIRE_STACK:
        pytest.fail(message)
    pytest.skip(message)


def _cookie_header(name: str, value: str) -> dict[str, str]:
    return {"Cookie": f"{name}={value}"} if value else {}


def _mirror_cookie_for_http_client(client: httpx.Client, name: str, value: str | None) -> None:
    if value:
        for domain in ("localhost.local", "localhost", "127.0.0.1"):
            try:
                client.cookies.delete(name, domain=domain, path="/")
            except Exception:
                pass
        client.cookies.set(name, value, path="/")


@pytest.fixture(scope="session", autouse=True)
def require_admin_password_in_ci():
    if RUNNING_IN_CI and REQUIRE_STACK and not ADMIN_PASSWORD:
        pytest.fail(
            "E2E_ADMIN_PASSWORD is empty inside CI with "
            "E2E_REQUIRE_STACK=1. Either provide the value as a "
            "repository secret (recommended) or drop "
            "E2E_REQUIRE_STACK=1 to silently skip E2E. It must "
            "match the BOOTSTRAP_ADMIN_PASSWORD used to create the "
            "admin with python -m app.bootstrap_admin."
        )


@pytest.fixture(scope="session")
def console_up():
    try:
        r = httpx.get(f"{CONSOLE}/healthz", timeout=3.0)
    except Exception as exc:
        _bail(
            f"E2E stack not reachable at {CONSOLE} ({type(exc).__name__}). "
            f"Bring it up with: "
            f"docker compose -f infra/docker-compose.yml --profile sap up -d --build"
        )
        return None
    if r.status_code != 200:
        _bail(
            f"E2E stack /healthz returned {r.status_code} at {CONSOLE} — "
            f"some services aren't healthy yet"
        )
    return CONSOLE


@pytest.fixture(scope="session")
def admin_session(console_up):
    if not ADMIN_PASSWORD:
        _bail(
            "E2E_ADMIN_PASSWORD is not set. Either set it to the "
            "BOOTSTRAP_ADMIN_PASSWORD used at stack startup, or "
            "unset E2E_REQUIRE_STACK to skip the E2E suite locally."
        )
    client = httpx.Client(base_url=console_up, timeout=15.0)
    login = client.get("/login")
    csrf = login.cookies.get("csrf_token") or client.cookies.get("csrf_token") or client.cookies.get("csrftoken") or ""
    _mirror_cookie_for_http_client(client, "csrf_token", csrf)
    headers = {"X-CSRF-Token": csrf, **_cookie_header("csrf_token", csrf)} if csrf else {}
    r = client.post(
        "/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        headers=headers,
    )
    if r.status_code != 200:
        _bail(
            f"E2E admin login failed: {r.status_code} {r.text[:200]}. "
            f"Check E2E_ADMIN_EMAIL / E2E_ADMIN_PASSWORD."
        )
    for cookie_name in ("csrf_token", "mod_session", "refresh_token"):
        _mirror_cookie_for_http_client(
            client,
            cookie_name,
            r.cookies.get(cookie_name) or client.cookies.get(cookie_name),
        )
    yield client
    client.close()
