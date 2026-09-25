from __future__ import annotations

import pytest

from tests.conftest import PRIORITY_CARTRIDGES, load_cartridge_app


@pytest.mark.parametrize("cartridge", PRIORITY_CARTRIDGES)
def test_health_endpoints_and_auth(cartridge: str) -> None:
    try:
        from fastapi.testclient import TestClient
    except ImportError:
        pytest.skip("fastapi not installed in this environment")

    main = load_cartridge_app(cartridge)
    client = TestClient(main.app)

    resp = client.get("/health")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("ok") is True
    assert body.get("service") == cartridge

    resp = client.get("/skills/entities")
    assert resp.status_code == 401, \
        f"{cartridge}: /skills/entities should be 401 without key, got {resp.status_code}"

    resp = client.get("/mcp/tools")
    assert resp.status_code == 401

    resp = client.get(
        "/skills/entities",
        headers={
            "X-Internal-Api-Key": "test-secret-key-not-default",
            "X-Internal-Service": "console",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "entities" in body
    assert isinstance(body["entities"], list)
    assert len(body["entities"]) >= 5

    resp = client.get(
        "/skills/entities",
        headers={
            "X-Internal-Api-Key": "wrong",
            "X-Internal-Service": "console",
        },
    )
    assert resp.status_code == 401


@pytest.mark.parametrize("cartridge", PRIORITY_CARTRIDGES)
def test_cartridge_connection_check_degraded_without_creds(cartridge: str) -> None:
    try:
        from fastapi.testclient import TestClient
    except ImportError:
        pytest.skip("fastapi not installed in this environment")

    main = load_cartridge_app(cartridge)
    client = TestClient(main.app)

    resp = client.get(
        f"/health/{cartridge}",
        headers={
            "X-Internal-Api-Key": "test-secret-key-not-default",
            "X-Internal-Service": "console",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("status") in {"degraded", "ok", "error"}
    if body.get("status") == "degraded":
        assert body.get("configured") is False
        assert isinstance(body.get("missing"), list)
        assert len(body["missing"]) >= 1
