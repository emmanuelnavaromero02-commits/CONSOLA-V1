from __future__ import annotations

import importlib
import os
import sys

import pytest

from tests.conftest import CARTRIDGES_ROOT, load_cartridge_app


SAP_CARTRIDGES = ("sap_hcm", "sap_s4hana", "sap_successfactors", "sap_b1")
SENTINEL = "__internal_api_key_not_configured__"
STRONG_KEY = "long-strong-key-abcdef1234567890"


def _purge_app_modules() -> None:
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]


def _load_security_module(cartridge: str):
    cart_dir = CARTRIDGES_ROOT / cartridge
    assert cart_dir.is_dir(), f"cartridge dir missing: {cart_dir}"

    _purge_app_modules()
    sys.path[:] = [p for p in sys.path if "/cartridges/" not in p]
    sys.path.insert(0, str(cart_dir))
    return importlib.import_module("app.security")


@pytest.mark.parametrize("cartridge", SAP_CARTRIDGES)
def test_get_internal_api_key_raises_when_unset(cartridge: str, monkeypatch):
    monkeypatch.delenv("INTERNAL_API_KEY", raising=False)
    security = _load_security_module(cartridge)

    with pytest.raises(RuntimeError, match="INTERNAL_API_KEY is not configured"):
        security.get_internal_api_key()


@pytest.mark.parametrize("cartridge", SAP_CARTRIDGES)
def test_get_internal_api_key_raises_on_insecure_default(cartridge: str, monkeypatch):
    monkeypatch.setenv("INTERNAL_API_KEY", "changeme")
    security = _load_security_module(cartridge)

    with pytest.raises(RuntimeError, match="insecure default"):
        security.get_internal_api_key()


@pytest.mark.parametrize("cartridge", SAP_CARTRIDGES)
def test_get_internal_api_key_returns_strong_key(cartridge: str, monkeypatch):
    monkeypatch.setenv("INTERNAL_API_KEY", STRONG_KEY)
    security = _load_security_module(cartridge)

    assert security.get_internal_api_key() == STRONG_KEY


@pytest.mark.parametrize("cartridge", SAP_CARTRIDGES)
def test_sentinel_string_not_accepted_as_valid_auth(cartridge: str, monkeypatch):
    monkeypatch.setenv("INTERNAL_API_KEY", STRONG_KEY)
    main = load_cartridge_app(cartridge)

    from fastapi.testclient import TestClient

    with TestClient(main.app, raise_server_exceptions=False) as client:
        resp = client.get(
            "/mcp/rpc/",
            headers={
                "X-Api-Key": SENTINEL,
                "X-Internal-Service": "console",
            },
        )

    assert resp.status_code == 401
