"""End-to-end smoke for each priority cartridge:

* GET /health returns 200 without a key (public liveness probe).
* GET /skills/entities returns 401 with no API key.
* GET /skills/entities returns 200 when the correct key is supplied.

This uses ``fastapi.testclient.TestClient`` so no real HTTP server is needed.
"""
from __future__ import annotations

import pytest

from tests.conftest import PRIORITY_CARTRIDGES, load_cartridge_app


# Tests run sequentially per cartridge to avoid module-name collisions
# (every cartridge ships an "app" package).
@pytest.mark.parametrize("cartridge", PRIORITY_CARTRIDGES)
def test_health_endpoints_and_auth(cartridge: str) -> None:
    try:
        from fastapi.testclient import TestClient
    except ImportError:
        pytest.skip("fastapi not installed in this environment")

    main = load_cartridge_app(cartridge)
    client = TestClient(main.app)

    # 1) liveness is public. v1.43.2 (P1-5): conftest seeds
    # app.state.startup_ok = True so /health returns 200 without
    # needing a lifespan-aware TestClient context.
    resp = client.get("/health")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("ok") is True
    assert body.get("service") == cartridge

    # 2) /skills/entities REQUIRES the api key
    resp = client.get("/skills/entities")
    assert resp.status_code == 401, \
        f"{cartridge}: /skills/entities should be 401 without key, got {resp.status_code}"

    # 3) MCP REST endpoints also require the key
    resp = client.get("/mcp/tools")
    assert resp.status_code == 401

    # 4) With the correct key + service header /skills/entities returns the catalogue
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

    # 5) Wrong key still rejected
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
    """When SAP credentials are not configured the client must report
    ``status=degraded`` and ``configured=false`` — never inject fake data."""
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
