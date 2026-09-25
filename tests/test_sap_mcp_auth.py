from __future__ import annotations

import pytest

from tests.conftest import load_cartridge_app


API_KEY = "test-secret-key-not-default"
VALID_HEADERS = {
    "X-Api-Key": API_KEY,
    "X-Internal-Service": "console",
}


def _client_for(cartridge: str):
    from fastapi.testclient import TestClient

    main = load_cartridge_app(cartridge)
    return TestClient(main.app, raise_server_exceptions=False)


def test_sap_hcm_mcp_rpc_requires_auth():
    with _client_for("sap_hcm") as client:
        resp = client.get("/mcp/rpc/")

    assert resp.status_code == 401


def test_sap_s4hana_mcp_rpc_requires_auth():
    with _client_for("sap_s4hana") as client:
        resp = client.get("/mcp/rpc/")

    assert resp.status_code == 401


def test_sap_successfactors_mcp_rpc_requires_auth():
    with _client_for("sap_successfactors") as client:
        resp = client.get("/mcp/rpc/")

    assert resp.status_code == 401


def test_sap_b1_mcp_rpc_requires_auth():
    with _client_for("sap_b1") as client:
        resp = client.get("/mcp/rpc/")

    assert resp.status_code == 401


@pytest.mark.parametrize("cartridge", ("sap_hcm", "sap_s4hana", "sap_successfactors", "sap_b1"))
def test_sap_mcp_rpc_accepts_valid_key(cartridge: str):
    with _client_for(cartridge) as client:
        resp = client.get("/mcp/rpc/", headers=VALID_HEADERS)

    assert resp.status_code != 401
