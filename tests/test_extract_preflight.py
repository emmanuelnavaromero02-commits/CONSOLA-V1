"""
Pre-flight contract:

* POST /entities/{e}/extract  without SAP creds → 503 + degraded JSON,
  *not* 500.
* POST /extract-all            without SAP creds → 503.
* The reported ``missing`` list names every component that needs config
  so the caller doesn't confuse a missing SAP cred with e.g. a Postgres
  outage.
"""
from __future__ import annotations

import pytest

from tests.conftest import PRIORITY_CARTRIDGES, load_cartridge_app

API_KEY = "test-secret-key-not-default"
AUTH_HEADER = {"X-Internal-Api-Key": API_KEY, "X-Internal-Service": "console"}


def _pick_known_entity(client) -> str:
    catalogue = client.get("/entities", headers=AUTH_HEADER).json()["entities"]
    assert catalogue, "catalogue empty — cannot run preflight test"
    return catalogue[0]["entity"]


@pytest.mark.parametrize("cartridge", PRIORITY_CARTRIDGES)
def test_extract_without_sap_returns_503(cartridge: str, monkeypatch) -> None:
    """SAP creds absent (env empty) → /extract responds 503, never 500."""
    from fastapi.testclient import TestClient

    # Force all SAP env vars unset for the priority cartridges
    for env_var in [
        # SuccessFactors
        "SF_BASE_URL", "SF_COMPANY_ID", "SF_CLIENT_ID", "SF_CLIENT_SECRET", "SF_TOKEN_URL",
        # HCM
        "SAP_HCM_BASE_URL", "SAP_HCM_USER", "SAP_HCM_PASS",
        # S/4HANA — canonical + legacy
        "SAP_S4_BASE_URL", "SAP_S4_USER", "SAP_S4_PASS",
        "S4_BASE_URL", "S4_USER", "S4_PASS",
    ]:
        monkeypatch.delenv(env_var, raising=False)

    main = load_cartridge_app(cartridge)
    client = TestClient(main.app)
    entity = _pick_known_entity(client)

    resp = client.post(f"/entities/{entity}/extract", headers=AUTH_HEADER)
    assert resp.status_code == 503, \
        f"{cartridge}: extract without SAP creds returned {resp.status_code}, want 503"
    body = resp.json()
    assert body.get("status") == "degraded"
    assert body.get("configured") is False
    assert isinstance(body.get("missing"), list) and body["missing"], \
        f"{cartridge}: 'missing' list is empty"
    # Should list at least one SAP env var
    sap_prefixes = ("SF_", "SAP_HCM_", "SAP_S4_")
    assert any(any(m.startswith(p) for p in sap_prefixes) for m in body["missing"]), \
        f"{cartridge}: missing list {body['missing']} does not name any SAP env var"


@pytest.mark.parametrize("cartridge", PRIORITY_CARTRIDGES)
def test_extract_all_without_sap_returns_503(cartridge: str, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    for env_var in [
        "SF_BASE_URL", "SF_COMPANY_ID", "SF_CLIENT_ID", "SF_CLIENT_SECRET", "SF_TOKEN_URL",
        "SAP_HCM_BASE_URL", "SAP_HCM_USER", "SAP_HCM_PASS",
        "SAP_S4_BASE_URL", "SAP_S4_USER", "SAP_S4_PASS",
        "S4_BASE_URL", "S4_USER", "S4_PASS",
    ]:
        monkeypatch.delenv(env_var, raising=False)

    main = load_cartridge_app(cartridge)
    client = TestClient(main.app)

    resp = client.post("/extract-all", headers=AUTH_HEADER)
    assert resp.status_code == 503, \
        f"{cartridge}: extract-all without SAP creds returned {resp.status_code}, want 503"
    body = resp.json()
    assert body.get("status") == "degraded"
    assert body.get("configured") is False


@pytest.mark.parametrize("cartridge", PRIORITY_CARTRIDGES)
def test_extract_unknown_entity_returns_404(cartridge: str) -> None:
    from fastapi.testclient import TestClient

    main = load_cartridge_app(cartridge)
    client = TestClient(main.app)
    resp = client.post("/entities/__NOT_THERE__/extract", headers=AUTH_HEADER)
    assert resp.status_code == 404


@pytest.mark.parametrize("cartridge", PRIORITY_CARTRIDGES)
def test_extract_without_api_key_returns_401(cartridge: str) -> None:
    from fastapi.testclient import TestClient

    main = load_cartridge_app(cartridge)
    client = TestClient(main.app)
    resp = client.post("/entities/User/extract")
    assert resp.status_code == 401
