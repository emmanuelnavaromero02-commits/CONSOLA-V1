"""Sprint v1.44.3.3 Task C — cartridge /skills/list + /healthz contracts.

The user's brief reported all four MCP cartridges (replicon,
sap_hcm, sap_s4hana, sap_successfactors) returning 404 on
``GET /skills/list``. The pre-existing audit confirmed that
``/healthz`` was also missing (``/health`` was present but
gates on real startup state — it's not the simple yes/no
probe the orchestrator + E2E suite expect).

This file pins:

  - ``GET /skills/list`` exists on every cartridge,
  - it's protected by the same ``verify_api_key`` dependency as
    every other ``/skills/*`` route (unauth → 401),
  - the response shape includes ``service`` (matching the
    cartridge id) and ``skills`` (a non-empty list of
    {name, method, summary}),
  - ``/list`` does NOT appear in its own output (avoid the
    catalog listing itself recursively),
  - ``GET /healthz`` exists, is PUBLIC (no auth header
    required), and returns ``{"ok": true, "service": <id>}``.

The tests use ``load_cartridge_app`` from conftest.py to load
each cartridge's app cleanly. Same pattern as
tests/test_replicon_mcp_auth.py.
"""
from __future__ import annotations

import pytest

from tests.conftest import load_cartridge_app


API_KEY = "test-secret-key-not-default"
VALID_HEADERS = {
    "X-Api-Key": API_KEY,
    "X-Internal-Service": "console",
}


CARTRIDGES = ("replicon", "sap_hcm", "sap_s4hana", "sap_successfactors")


def _client(cartridge_id: str):
    from fastapi.testclient import TestClient

    main = load_cartridge_app(cartridge_id)
    return TestClient(main.app, raise_server_exceptions=False)


# ── /skills/list ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("cartridge_id", CARTRIDGES)
def test_skills_list_requires_auth(cartridge_id):
    """Anonymous → 401. The skill list is privileged metadata —
    the orchestrator authenticates with an internal API key
    before discovering capabilities."""
    with _client(cartridge_id) as client:
        resp = client.get("/skills/list")
    assert resp.status_code == 401, (
        f"GET /skills/list on {cartridge_id} returned "
        f"{resp.status_code} without an API key. Expected 401 "
        f"(verify_api_key dependency must be applied)."
    )


@pytest.mark.parametrize("cartridge_id", CARTRIDGES)
def test_skills_list_returns_200_authenticated(cartridge_id):
    with _client(cartridge_id) as client:
        resp = client.get("/skills/list", headers=VALID_HEADERS)
    assert resp.status_code == 200, (
        f"GET /skills/list on {cartridge_id} returned "
        f"{resp.status_code} with valid API key. Body: {resp.text[:200]}"
    )


@pytest.mark.parametrize("cartridge_id", CARTRIDGES)
def test_skills_list_response_shape(cartridge_id):
    """The body must include ``service`` (cartridge id) and
    ``skills`` (a list). The shape is consumed by the console's
    capability discovery."""
    with _client(cartridge_id) as client:
        body = client.get("/skills/list", headers=VALID_HEADERS).json()

    assert body.get("service") == cartridge_id, (
        f"skills/list service field on {cartridge_id} is "
        f"{body.get('service')!r}, expected {cartridge_id!r}"
    )
    skills = body.get("skills")
    assert isinstance(skills, list), (
        f"skills/list on {cartridge_id} must return a list under "
        f"``skills``; got {type(skills).__name__}"
    )
    assert len(skills) > 0, (
        f"skills/list on {cartridge_id} returned an empty list — "
        f"the cartridge must expose at least one skill (test_connection)"
    )
    # Sanity check shape of one entry. v1.44.3.3 R-Mac-Round-3
    # Task F: ``description`` is the canonical key; ``summary``
    # is aliased for one sprint and will go away in v1.44.4.
    sample = skills[0]
    for key in ("name", "method", "description", "summary"):
        assert key in sample, (
            f"skills/list entry on {cartridge_id} missing key {key!r}: "
            f"{sample}"
        )
    assert sample["description"] == sample["summary"], (
        f"skills/list ``description`` and ``summary`` must hold the "
        f"same value for the one-sprint alias to be transparent. "
        f"Got: description={sample['description']!r} vs "
        f"summary={sample['summary']!r}"
    )


@pytest.mark.parametrize("cartridge_id", CARTRIDGES)
def test_skills_list_does_not_list_itself(cartridge_id):
    """Catalog endpoints that include themselves create
    confusing UI loops. Filter ``/list`` out of its own output."""
    with _client(cartridge_id) as client:
        body = client.get("/skills/list", headers=VALID_HEADERS).json()
    names = [s["name"] for s in body.get("skills", [])]
    assert "/skills/list" not in names and "/list" not in names, (
        f"skills/list on {cartridge_id} listed itself: {names}"
    )


@pytest.mark.parametrize("cartridge_id", CARTRIDGES)
def test_skills_list_includes_test_connection(cartridge_id):
    """Spot-check: every cartridge exposes test_connection (the
    auditor P1 endpoint added in v1.41.0). If discovery omits it
    the introspection logic is broken."""
    with _client(cartridge_id) as client:
        body = client.get("/skills/list", headers=VALID_HEADERS).json()
    names = [s["name"] for s in body.get("skills", [])]
    assert any("test_connection" in name for name in names), (
        f"skills/list on {cartridge_id} did not surface "
        f"test_connection. Names: {names}"
    )


# ── /healthz ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("cartridge_id", CARTRIDGES)
def test_healthz_returns_200_no_auth(cartridge_id):
    """``/healthz`` must be PUBLIC — Kubernetes liveness probes
    don't send API keys. Adding auth would knock the cartridge
    out of rotation every time the probe fires."""
    with _client(cartridge_id) as client:
        resp = client.get("/healthz")
    assert resp.status_code == 200, (
        f"GET /healthz on {cartridge_id} returned {resp.status_code}. "
        f"Liveness probes don't carry auth — endpoint must be public."
    )


@pytest.mark.parametrize("cartridge_id", CARTRIDGES)
def test_healthz_response_shape(cartridge_id):
    """``{ok: true, service: <id>}`` is the wire shape every
    orchestrator key off."""
    with _client(cartridge_id) as client:
        body = client.get("/healthz").json()
    assert body.get("ok") is True, (
        f"/healthz on {cartridge_id} must return ``ok: true``; got {body}"
    )
    assert body.get("service") == cartridge_id, (
        f"/healthz on {cartridge_id} service field is "
        f"{body.get('service')!r}; expected {cartridge_id!r}"
    )


@pytest.mark.parametrize("cartridge_id", CARTRIDGES)
def test_healthz_independent_of_startup_state(cartridge_id):
    """``/health`` gates on app.state.startup_ok; ``/healthz``
    must NOT — it's a yes/no liveness probe, NOT a readiness
    probe. Flip startup_ok off and confirm /healthz still 200s."""
    from fastapi.testclient import TestClient

    main = load_cartridge_app(cartridge_id)
    # Force a failed-startup state.
    main.app.state.startup_ok = False
    main.app.state.startup_errors = ["forced for test"]
    try:
        with TestClient(main.app, raise_server_exceptions=False) as client:
            resp = client.get("/healthz")
        assert resp.status_code == 200, (
            f"/healthz on {cartridge_id} returned {resp.status_code} "
            f"with startup_ok=False. /healthz must stay 200 regardless "
            f"of readiness — /health is the readiness probe."
        )
    finally:
        # Restore for any downstream test that shares the cached app.
        main.app.state.startup_ok = True
        main.app.state.startup_errors = []
