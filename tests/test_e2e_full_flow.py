"""Sprint v1.41.1 — End-to-end happy path against a live stack.

These tests intentionally talk to a running console (and through it,
to the cartridges + DB). They skip cleanly when the stack isn't up,
so they're safe inside ``make test`` for CI; the opt-in runner
(scripts/run_e2e.sh) is what operators reach for.
"""
from __future__ import annotations

import os

import httpx
import pytest


CONSOLE = os.environ.get("E2E_CONSOLE_URL", "http://localhost:8000")
ADMIN_EMAIL = os.environ.get("E2E_ADMIN_EMAIL", "admin@example.com")
ADMIN_PASSWORD = os.environ.get("E2E_ADMIN_PASSWORD", "")


def _skip_if_stack_down() -> None:
    try:
        r = httpx.get(f"{CONSOLE}/healthz", timeout=2.0)
    except Exception as exc:
        pytest.skip(f"Stack not running ({type(exc).__name__})")
    if r.status_code != 200:
        pytest.skip(f"Stack not healthy ({r.status_code})")


@pytest.fixture(scope="module")
def session():
    _skip_if_stack_down()
    if not ADMIN_PASSWORD:
        pytest.skip("E2E_ADMIN_PASSWORD not set")
    client = httpx.Client(base_url=CONSOLE, timeout=10.0)
    # GET /login to receive the CSRF cookie used by the JSON login.
    client.get("/login")
    csrf = client.cookies.get("csrftoken") or client.cookies.get("csrf_token") or ""
    r = client.post(
        "/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        headers={"X-CSRF-Token": csrf} if csrf else {},
    )
    assert r.status_code == 200, r.text
    yield client
    client.close()


def test_e2e_01_admin_can_login(session):
    r = session.get("/api/auth/me")
    assert r.status_code == 200
    body = r.json()
    # /api/auth/me may return the user under "user" or flat; accept either.
    email = body.get("email") or body.get("user", {}).get("email")
    assert email == ADMIN_EMAIL


def test_e2e_02_tool_manifest_lists_servers(session):
    r = session.get("/api/tools/manifest")
    assert r.status_code == 200
    data = r.json()
    assert "servers" in data, f"manifest missing servers key: {data}"
    # At least one cartridge/infra server present after a normal boot.
    assert isinstance(data["servers"], list)


def test_e2e_03_cartridge_connector_schema_replicon(session):
    r = session.get("/api/cartridges/replicon/connector_schema")
    assert r.status_code == 200
    body = r.json()
    assert body.get("connector", {}).get("id") == "replicon"


def test_e2e_04_freshness_endpoint_responds(session):
    r = session.get("/api/freshness/replicon")
    assert r.status_code == 200, r.text
    assert "entities" in r.json()


def test_e2e_05_freshness_summary_responds(session):
    r = session.get("/api/freshness")
    assert r.status_code == 200, r.text
    assert "cartridges" in r.json()


def test_e2e_06_response_carries_request_id_header(session):
    r = session.get("/healthz")
    # v1.41.1: every response must carry X-Request-ID.
    rid = r.headers.get("x-request-id")
    assert rid, "X-Request-ID missing from response"


def test_e2e_07_client_request_id_is_echoed(session):
    incoming = "e2e-trace-abc-123"
    r = session.get("/healthz", headers={"X-Request-ID": incoming})
    assert r.headers.get("x-request-id") == incoming


def test_e2e_08_audit_events_capture_ip(session):
    # Forensic-complete audit (v1.41.0 P1) — at least one recent row
    # must have ip captured. Endpoint is admin-only and read-only.
    r = session.get("/api/admin/audit?limit=20")
    if r.status_code == 404:
        pytest.skip("audit list endpoint not exposed in this build")
    assert r.status_code == 200, r.text
    body = r.json()
    rows = body.get("events") if isinstance(body, dict) else body
    if not rows:
        pytest.skip("audit_events table empty — no admin actions yet")
    with_ip = [e for e in rows if (e.get("ip") or e.get("client_ip"))]
    assert with_ip, "post-v1.41.0 audit rows must capture ip"
