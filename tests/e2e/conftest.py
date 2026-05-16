"""Sprint v1.43.1 (Codex P0-5 + Claude) — E2E sandbox fixtures.

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


def _bail(message: str) -> None:
    """Skip in dev, fail in CI — single code path so the two modes
    can't drift."""
    if REQUIRE_STACK:
        pytest.fail(message)
    pytest.skip(message)


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
    client.get("/login")          # warm CSRF cookie
    csrf = client.cookies.get("csrftoken") or client.cookies.get("csrf_token") or ""
    r = client.post(
        "/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        headers={"X-CSRF-Token": csrf} if csrf else {},
    )
    if r.status_code != 200:
        _bail(
            f"E2E admin login failed: {r.status_code} {r.text[:200]}. "
            f"Check E2E_ADMIN_EMAIL / E2E_ADMIN_PASSWORD."
        )
    yield client
    client.close()
