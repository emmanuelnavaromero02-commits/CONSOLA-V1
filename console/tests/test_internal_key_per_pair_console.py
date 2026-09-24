"""Sprint v1.12 — console: verify_internal_api_key accepts per-pair keys + legacy.

Console exposes /internal/* endpoints to workspace and to the four
cartridges. Each pair has its own INTERNAL_API_KEY_*_TO_CONSOLE secret;
the legacy shared INTERNAL_API_KEY also still works during migration.
"""
from __future__ import annotations

import importlib
import sys
import types

import pytest
from fastapi import HTTPException


LEGACY     = "legacy_internal_key_with_more_than_thirty_two_characters_aaaa"
WS_KEY     = "workspace_to_console_dedicated_key_64_chars_xxxxxxxxxxxxxxxxxx"
CART_KEY   = "cartridge_to_console_dedicated_key_64_chars_yyyyyyyyyyyyyyyyy"
REPLICON_KEY = "replicon_to_console_dedicated_key_64_chars_aaaaaaaaaaaaaaa"
HUBSPOT_KEY = "hubspot_to_console_dedicated_key_64_chars_bbbbbbbbbbbbbbbb"
SAP_HCM_KEY = "sap_hcm_to_console_dedicated_key_64_chars_ccccccccccccccc"
SAP_S4HANA_KEY = "sap_s4hana_to_console_dedicated_key_64_chars_dddddddddddd"
SAP_SUCCESSFACTORS_KEY = "sap_successfactors_to_console_dedicated_key_64_chars"
SALESFORCE_KEY = "salesforce_to_console_dedicated_key_64_chars_zzzzzzzzzzz"
SAP_B1_KEY = "sap_b1_to_console_dedicated_key_64_chars_eeeeeeeeeeeeeeee"


def _module(**attrs):
    mod = types.ModuleType("stub")
    for key, value in attrs.items():
        setattr(mod, key, value)
    return mod


@pytest.fixture()
def auth_module(monkeypatch):
    monkeypatch.setenv("INTERNAL_API_KEY", LEGACY)
    monkeypatch.setenv("INTERNAL_API_KEY_WORKSPACE_TO_CONSOLE", WS_KEY)
    monkeypatch.setenv("INTERNAL_API_KEY_CARTRIDGE_TO_CONSOLE", CART_KEY)
    monkeypatch.setenv("INTERNAL_API_KEY_REPLICON_TO_CONSOLE", REPLICON_KEY)
    monkeypatch.setenv("INTERNAL_API_KEY_HUBSPOT_TO_CONSOLE", HUBSPOT_KEY)
    monkeypatch.setenv("INTERNAL_API_KEY_SAP_HCM_TO_CONSOLE", SAP_HCM_KEY)
    monkeypatch.setenv("INTERNAL_API_KEY_SAP_S4HANA_TO_CONSOLE", SAP_S4HANA_KEY)
    monkeypatch.setenv("INTERNAL_API_KEY_SAP_SUCCESSFACTORS_TO_CONSOLE", SAP_SUCCESSFACTORS_KEY)
    monkeypatch.setenv("INTERNAL_API_KEY_SALESFORCE_TO_CONSOLE", SALESFORCE_KEY)
    monkeypatch.setenv("INTERNAL_API_KEY_SAP_B1_TO_CONSOLE", SAP_B1_KEY)
    monkeypatch.setitem(sys.modules, "bcrypt", _module())
    monkeypatch.setitem(sys.modules, "asyncpg", _module())
    import app.services as _svc_pkg
    _original = sys.modules.get("app.services.auth")
    _original_pkg_attr = getattr(_svc_pkg, "auth", None)
    sys.modules.pop("app.services.auth", None)
    auth = importlib.import_module("app.services.auth")
    sys.modules["app.services.auth"] = auth
    setattr(_svc_pkg, "auth", auth)
    yield auth
    if _original is not None:
        sys.modules["app.services.auth"] = _original
        if _original_pkg_attr is not None:
            setattr(_svc_pkg, "auth", _original_pkg_attr)
    else:
        sys.modules.pop("app.services.auth", None)


def test_workspace_pair_key_accepted(auth_module):
    auth_module.verify_internal_api_key(x_api_key=WS_KEY, x_internal_service="workspace")


def test_cartridge_pair_key_accepted_per_cartridge(auth_module):
    pairs = {
        "replicon": REPLICON_KEY,
        "cartridge-replicon": REPLICON_KEY,
        "hubspot": HUBSPOT_KEY,
        "cartridge-hubspot": HUBSPOT_KEY,
        "sap_hcm": SAP_HCM_KEY,
        "cartridge-sap_hcm": SAP_HCM_KEY,
        "sap_s4hana": SAP_S4HANA_KEY,
        "cartridge-sap_s4hana": SAP_S4HANA_KEY,
        "sap_successfactors": SAP_SUCCESSFACTORS_KEY,
        "cartridge-sap_successfactors": SAP_SUCCESSFACTORS_KEY,
        "salesforce": SALESFORCE_KEY,
        "cartridge-salesforce": SALESFORCE_KEY,
        "sap_b1": SAP_B1_KEY,
        "cartridge-sap_b1": SAP_B1_KEY,
    }
    for service, key in pairs.items():
        auth_module.verify_internal_api_key(x_api_key=key, x_internal_service=service)


def test_generic_cartridge_key_rejected_for_all_builtin_cartridges(auth_module, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    for service in (
        "replicon",
        "cartridge-replicon",
        "hubspot",
        "cartridge-hubspot",
        "sap_hcm",
        "cartridge-sap_hcm",
        "sap_s4hana",
        "cartridge-sap_s4hana",
        "sap_successfactors",
        "cartridge-sap_successfactors",
        "salesforce",
        "cartridge-salesforce",
        "sap_b1",
        "cartridge-sap_b1",
    ):
        with pytest.raises(HTTPException) as exc:
            auth_module.verify_internal_api_key(x_api_key=CART_KEY, x_internal_service=service)
        assert exc.value.status_code == 403


def test_salesforce_uses_dedicated_console_key(auth_module):
    auth_module.verify_internal_api_key(
        x_api_key=SALESFORCE_KEY,
        x_internal_service="cartridge-salesforce",
    )
    with pytest.raises(HTTPException) as exc:
        auth_module.verify_internal_api_key(
            x_api_key=CART_KEY,
            x_internal_service="cartridge-salesforce",
        )
    assert exc.value.status_code == 403


def test_workspace_key_does_not_authorize_cartridge_caller(auth_module):
    with pytest.raises(HTTPException) as exc:
        auth_module.verify_internal_api_key(x_api_key=WS_KEY, x_internal_service="cartridge-replicon")
    assert exc.value.status_code == 403


def test_legacy_key_still_accepted_outside_production(auth_module, monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    auth_module.verify_internal_api_key(x_api_key=LEGACY, x_internal_service="workspace")
    auth_module.verify_internal_api_key(x_api_key=LEGACY, x_internal_service="cartridge-replicon")


def test_legacy_key_rejected_in_production(auth_module, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(HTTPException) as exc:
        auth_module.verify_internal_api_key(x_api_key=LEGACY, x_internal_service="workspace")
    assert exc.value.status_code == 403


def test_wrong_key_rejected(auth_module):
    with pytest.raises(HTTPException) as exc:
        auth_module.verify_internal_api_key(x_api_key="garbage", x_internal_service="workspace")
    assert exc.value.status_code == 403


def test_missing_service_rejected(auth_module):
    with pytest.raises(HTTPException) as exc:
        auth_module.verify_internal_api_key(x_api_key=LEGACY, x_internal_service=None)
    assert exc.value.status_code == 403


def test_unknown_service_rejected(auth_module):
    with pytest.raises(HTTPException) as exc:
        auth_module.verify_internal_api_key(x_api_key=LEGACY, x_internal_service="rogue")
    assert exc.value.status_code == 403
