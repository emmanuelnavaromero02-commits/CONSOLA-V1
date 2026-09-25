from __future__ import annotations

import os
import uuid

import httpx
import pytest


_REQUIRE_STACK = os.environ.get("E2E_REQUIRE_STACK", "").strip() == "1"


def _bail_or_skip(message: str) -> None:
    if _REQUIRE_STACK:
        pytest.fail(message)
    pytest.skip(message)


def test_full_flow_01_admin_session_and_request_id(admin_session):
    r = admin_session.get("/api/me")
    assert r.status_code == 200, r.text
    assert r.headers.get("x-request-id"), "X-Request-ID missing on /api/me"


def test_full_flow_02_tool_manifest_lists_cartridges(admin_session):
    r = admin_session.get("/api/tools/manifest")
    assert r.status_code == 200, r.text
    data = r.json()
    server_ids = set(data.get("servers") or {}).union({
        s.get("id") for s in data.get("servers") or [] if isinstance(s, dict)
    })
    raw = r.text.lower()
    for cart in ("replicon", "hubspot", "sap_hcm", "sap_s4hana", "sap_successfactors"):
        assert cart in raw, f"manifest does not mention cartridge {cart!r}"


def test_full_flow_03_freshness_endpoint_responds(admin_session):
    r = admin_session.get("/api/freshness/replicon")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "entities" in body
    assert isinstance(body["entities"], list)


def test_full_flow_04_copilot_conversation_round_trip(admin_session):
    title = f"e2e-{uuid.uuid4().hex[:8]}"
    csrf = admin_session.cookies.get("csrftoken") or admin_session.cookies.get("csrf_token") or ""
    r = admin_session.post(
        "/api/copilot/conversations",
        json={"title": title},
        headers={"X-CSRF-Token": csrf} if csrf else {},
    )
    if r.status_code == 403:
        _bail_or_skip(
            "POST /api/copilot/conversations returned 403 — "
            "missing copilot.use permission or LLM key in env"
        )
    assert r.status_code == 200, r.text
    conv_id = r.json()["id"]

    r = admin_session.post(
        f"/api/copilot/conversations/{conv_id}/messages",
        json={"message": "list available cartridges"},
        headers={"X-CSRF-Token": csrf} if csrf else {},
    )
    if r.status_code in (502, 503):
        _bail_or_skip(
            f"LLM provider returned {r.status_code} — check "
            f"ANTHROPIC_API_KEY in the worker env"
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "reply" in body
    assert "citations" in body
    assert isinstance(body["citations"], list)


def test_full_flow_05_audit_event_carries_ip_user_agent(admin_session):
    r = admin_session.get("/security/audit?limit=20")
    if r.status_code == 404:
        _bail_or_skip(
            "GET /security/audit returned 404 — this build profile "
            "does not expose the audit list endpoint"
        )
    if r.status_code != 200:
        _bail_or_skip(
            f"GET /security/audit returned {r.status_code} — "
            "audit surface is degraded"
        )
    body = r.json()
    rows = body.get("events") if isinstance(body, dict) else body
    if not rows:
        _bail_or_skip(
            "audit_events is empty after the conversation in test 04 — "
            "the audit pipeline is degraded"
        )
    with_ip = [e for e in rows if (e.get("ip") or e.get("client_ip"))]
    assert with_ip, (
        "post-v1.41.0 audit rows must populate ip — none of the "
        f"{len(rows)} latest rows have it"
    )


def test_full_flow_06_unauth_endpoint_still_carries_request_id():
    base = os.environ.get("E2E_CONSOLE_URL", "http://localhost:8000")
    try:
        r = httpx.get(f"{base}/api/whatever-unauth", timeout=3.0)
    except Exception as exc:
        _bail_or_skip(
            f"{base} not reachable for the unauth-header probe "
            f"({type(exc).__name__})"
        )
        return
    assert r.headers.get("x-request-id"), (
        "v1.42.1 invariant: every response — including 401 — must carry "
        "X-Request-ID. None present on /api/whatever-unauth"
    )


def test_full_flow_07_cartridge_unauth_returns_401_with_header():
    seen_any = False
    for cart, port in [
        ("replicon",            8201),
        ("sap_hcm",             8202),
        ("sap_successfactors",  8203),
        ("sap_s4hana",          8204),
    ]:
        try:
            r = httpx.post(f"http://localhost:{port}/mcp/invoke", json={}, timeout=3.0)
        except Exception as exc:
            if _REQUIRE_STACK:
                pytest.fail(
                    f"{cart} not reachable on port {port} ({type(exc).__name__}) "
                    f"— SAP profile likely didn't come up"
                )
            continue
        seen_any = True
        assert r.status_code == 401, (
            f"{cart}/mcp/invoke without auth must return 401 (got {r.status_code})"
        )
        assert r.headers.get("x-request-id"), (
            f"{cart} dropped X-Request-ID on a 401 response"
        )
    if not seen_any:
        _bail_or_skip(
            "no cartridge answered on 8201-8204 — stack is up but the "
            "SAP / Replicon profile isn't"
        )
