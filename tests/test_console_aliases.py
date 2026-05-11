"""Console-style alias endpoints share authentication semantics with /skills/*."""
from __future__ import annotations

import pytest

from tests.conftest import PRIORITY_CARTRIDGES, load_cartridge_app

API_KEY = "test-secret-key-not-default"
AUTH_HEADER = {"X-Internal-Api-Key": API_KEY}


@pytest.mark.parametrize("cartridge", PRIORITY_CARTRIDGES)
def test_console_alias_auth_matrix(cartridge: str) -> None:
    from fastapi.testclient import TestClient

    main = load_cartridge_app(cartridge)
    client = TestClient(main.app)

    # /health stays public
    assert client.get("/health").status_code == 200

    # Every aliased route rejects unauth requests
    unauth_targets = [
        ("GET", "/entities"),
        ("GET", "/entities/User/schema"),
        ("GET", "/entities/User/preview"),
        ("POST", "/entities/User/extract"),
        ("POST", "/extract-all"),
        ("GET", "/runs"),
        ("GET", "/runs/latest"),
        ("GET", "/watermarks"),
    ]
    for method, path in unauth_targets:
        resp = client.request(method, path)
        assert resp.status_code == 401, \
            f"{cartridge}: {method} {path} expected 401, got {resp.status_code}"


@pytest.mark.parametrize("cartridge", PRIORITY_CARTRIDGES)
def test_console_entities_with_key(cartridge: str) -> None:
    from fastapi.testclient import TestClient

    main = load_cartridge_app(cartridge)
    client = TestClient(main.app)

    resp = client.get("/entities", headers=AUTH_HEADER)
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body.get("entities"), list)
    assert len(body["entities"]) >= 5


@pytest.mark.parametrize("cartridge", PRIORITY_CARTRIDGES)
def test_console_entity_schema(cartridge: str) -> None:
    from fastapi.testclient import TestClient

    main = load_cartridge_app(cartridge)
    client = TestClient(main.app)

    catalogue = client.get("/entities", headers=AUTH_HEADER).json()["entities"]
    assert catalogue, f"{cartridge}: empty catalogue"
    first = catalogue[0]["entity"]

    resp = client.get(f"/entities/{first}/schema", headers=AUTH_HEADER)
    assert resp.status_code == 200
    schema = resp.json()
    assert schema.get("entity") == first


@pytest.mark.parametrize("cartridge", PRIORITY_CARTRIDGES)
def test_console_unknown_entity_returns_404(cartridge: str) -> None:
    from fastapi.testclient import TestClient

    main = load_cartridge_app(cartridge)
    client = TestClient(main.app)

    resp = client.get("/entities/NoExiste42/schema", headers=AUTH_HEADER)
    assert resp.status_code == 404


@pytest.mark.parametrize("cartridge", PRIORITY_CARTRIDGES)
def test_console_alias_wrong_key(cartridge: str) -> None:
    from fastapi.testclient import TestClient

    main = load_cartridge_app(cartridge)
    client = TestClient(main.app)

    resp = client.get("/entities", headers={"X-Internal-Api-Key": "wrong"})
    assert resp.status_code == 401
