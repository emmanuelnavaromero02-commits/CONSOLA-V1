from __future__ import annotations

import pytest

from tests.conftest import load_cartridge_app


API_KEY = "test-secret-key-not-default"
VALID_HEADERS = {
    "X-Api-Key": API_KEY,
    "X-Internal-Service": "console",
}


CARTRIDGES = ("replicon", "hubspot", "banxico", "inegi", "sec_edgar", "sap_hcm", "sap_s4hana", "sap_successfactors", "sap_b1")


def _client(cartridge_id: str):
    from fastapi.testclient import TestClient

    main = load_cartridge_app(cartridge_id)
    return TestClient(main.app, raise_server_exceptions=False)


@pytest.mark.parametrize("cartridge_id", CARTRIDGES)
def test_skills_list_requires_auth(cartridge_id):
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
    with _client(cartridge_id) as client:
        body = client.get("/skills/list", headers=VALID_HEADERS).json()
    names = [s["name"] for s in body.get("skills", [])]
    assert "/skills/list" not in names and "/list" not in names, (
        f"skills/list on {cartridge_id} listed itself: {names}"
    )


@pytest.mark.parametrize("cartridge_id", CARTRIDGES)
def test_skills_list_includes_test_connection(cartridge_id):
    with _client(cartridge_id) as client:
        body = client.get("/skills/list", headers=VALID_HEADERS).json()
    names = [s["name"] for s in body.get("skills", [])]
    assert any("test_connection" in name for name in names), (
        f"skills/list on {cartridge_id} did not surface "
        f"test_connection. Names: {names}"
    )


@pytest.mark.parametrize("cartridge_id", CARTRIDGES)
def test_healthz_returns_200_no_auth(cartridge_id):
    with _client(cartridge_id) as client:
        resp = client.get("/healthz")
    assert resp.status_code == 200, (
        f"GET /healthz on {cartridge_id} returned {resp.status_code}. "
        f"Liveness probes don't carry auth — endpoint must be public."
    )


@pytest.mark.parametrize("cartridge_id", CARTRIDGES)
def test_healthz_response_shape(cartridge_id):
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
    from fastapi.testclient import TestClient

    main = load_cartridge_app(cartridge_id)
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
        main.app.state.startup_ok = True
        main.app.state.startup_errors = []
