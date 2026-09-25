"""Sprint v1.43.1 (P0-5) — E2E sandbox fixtures.

The pattern v1.41.1 introduced (``tests/test_e2e_full_flow.py``) skips
silently when the stack isn't up. That covers the local-dev case but
hides whether CI actually runs the E2E surface.

This module reuses the same probe + login fixtures BUT introduces an
``E2E_REQUIRE_STACK`` env-var contract:

  * ``E2E_REQUIRE_STACK=1`` (CI default) → if any required service is
    unreachable, the test FAILS with a clear remediation message
    instead of skipping silently.
  * Unset (local-dev default) → behave like the old fixture: skip
    cleanly so make test stays green offline.

That gives us a single source of truth (these fixtures) for both
modes without inventing two parallel test suites.
"""
from __future__ import annotations

import os

import httpx
import pytest


CONSOLE = os.environ.get("E2E_CONSOLE_URL", "http://localhost:8000")
ADMIN_EMAIL = os.environ.get("E2E_ADMIN_EMAIL", "admin@example.com")
ADMIN_PASSWORD = os.environ.get("E2E_ADMIN_PASSWORD", "")

# CI flips this to "1" so a misconfigured E2E job loudly fails.
REQUIRE_STACK = os.environ.get("E2E_REQUIRE_STACK", "").strip() == "1"
# Generic CI signal: GitHub Actions, GitLab CI, CircleCI, and most
# every CI provider sets ``CI=true``. We use it to distinguish a
# "running in CI without E2E creds" foot-gun from a "developer ran
# pytest at the project root" silent skip.
RUNNING_IN_CI = os.environ.get("CI", "").strip().lower() in {"true", "1", "yes"}


def _bail(message: str) -> None:
    """Skip in dev, fail in CI — single code path so the two modes
    can't drift."""
    if REQUIRE_STACK:
        pytest.fail(message)
    pytest.skip(message)


def _cookie_header(name: str, value: str) -> dict[str, str]:
    return {"Cookie": f"{name}={value}"} if value else {}


def _mirror_cookie_for_http_client(client: httpx.Client, name: str, value: str | None) -> None:
    """httpx honors Secure cookies and therefore will not replay them on
    http://localhost. The local stack can run COOKIE_SECURE=true to mimic
    production, so E2E mirrors response cookies as host-only test cookies."""
    if value:
        for domain in ("localhost.local", "localhost", "127.0.0.1"):
            try:
                client.cookies.delete(name, domain=domain, path="/")
            except Exception:
                pass
        client.cookies.set(name, value, path="/")


# v1.43.4 (L1): if E2E tests are being collected inside CI
# AND the admin password isn't set, fail the collection step
# loudly. Pre-v1.43.4 the silent skip path made it impossible to
# tell whether a green CI run had actually exercised the E2E
# surface — a regression that erased every cartridge endpoint
# would have shipped green.
#
# The fixture is autouse + session-scoped so it fires once per
# pytest session, before any test function runs. We only require
# the password when ``E2E_REQUIRE_STACK=1`` is ALSO set; CI runs
# that haven't yet wired up the live-stack step (e.g. unit-only
# matrix jobs) keep the old skip behaviour. The pair
# ``CI=true`` + ``E2E_REQUIRE_STACK=1`` + missing password is the
# misconfiguration we want to surface.
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
    """Stack-probe fixture. Returns the base URL once we've confirmed
    /healthz answers 200; otherwise skip or fail per REQUIRE_STACK."""
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
    """Authenticated httpx client with the admin session cookie."""
    if not ADMIN_PASSWORD:
        _bail(
            "E2E_ADMIN_PASSWORD is not set. Either set it to the "
            "BOOTSTRAP_ADMIN_PASSWORD used at stack startup, or "
            "unset E2E_REQUIRE_STACK to skip the E2E suite locally."
        )
    client = httpx.Client(base_url=console_up, timeout=15.0)
    login = client.get("/login")          # warm CSRF cookie
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
