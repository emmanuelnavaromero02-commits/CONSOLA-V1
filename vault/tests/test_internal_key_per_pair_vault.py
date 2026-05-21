"""Sprint v1.12 — vault: verify_api_key accepts per-pair keys + dev legacy.

Vault is called by console and mcp-infra. Each pair has its own
INTERNAL_API_KEY_*_TO_VAULT secret; the legacy shared INTERNAL_API_KEY
only works outside production.
"""
from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

import pytest
from fastapi import HTTPException


LEGACY      = "legacy_internal_key_with_more_than_thirty_two_characters_aaaa"
CONSOLE_KEY = "console_to_vault_dedicated_key_64_chars_xxxxxxxxxxxxxxxxxxxxxxx"
MCP_KEY     = "mcp_infra_to_vault_dedicated_key_64_chars_yyyyyyyyyyyyyyyyyyyy"


def _module(**attrs):
    mod = types.ModuleType("stub")
    for key, value in attrs.items():
        setattr(mod, key, value)
    return mod


@pytest.fixture()
def vault_main(monkeypatch):
    root = Path(__file__).resolve().parents[1]
    sys.path[:] = [p for p in sys.path if p != str(root)]
    sys.path.insert(0, str(root))
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("INTERNAL_API_KEY", LEGACY)
    monkeypatch.setenv("INTERNAL_API_KEY_CONSOLE_TO_VAULT",   CONSOLE_KEY)
    monkeypatch.setenv("INTERNAL_API_KEY_MCP_INFRA_TO_VAULT", MCP_KEY)
    # Stub out heavyweight third-party deps that vault's app/main.py imports
    # at module load. Their behavior isn't exercised by the verify tests.
    monkeypatch.setitem(sys.modules, "psycopg2", _module())
    monkeypatch.setitem(sys.modules, "yaml", _module(safe_load=lambda *a, **kw: {}))
    monkeypatch.setitem(
        sys.modules,
        "app.security",
        _module(get_internal_api_key=lambda: LEGACY),
    )
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    main = importlib.import_module("app.main")
    yield main
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]


def test_console_pair_key_accepted(vault_main):
    vault_main.verify_api_key(x_api_key=CONSOLE_KEY, x_internal_service="console")


def test_mcp_infra_pair_key_accepted(vault_main):
    vault_main.verify_api_key(x_api_key=MCP_KEY, x_internal_service="mcp-infra")


def test_console_key_does_not_authorize_mcp_infra(vault_main):
    with pytest.raises(HTTPException) as exc:
        vault_main.verify_api_key(x_api_key=CONSOLE_KEY, x_internal_service="mcp-infra")
    assert exc.value.status_code == 403


def test_legacy_key_accepted_for_any_whitelisted_caller(vault_main):
    vault_main.verify_api_key(x_api_key=LEGACY, x_internal_service="console")
    vault_main.verify_api_key(x_api_key=LEGACY, x_internal_service="workspace")
    vault_main.verify_api_key(x_api_key=LEGACY, x_internal_service="mcp-infra")


def test_legacy_key_rejected_in_production(monkeypatch):
    root = Path(__file__).resolve().parents[1]
    sys.path[:] = [p for p in sys.path if p != str(root)]
    sys.path.insert(0, str(root))
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("INTERNAL_API_KEY", LEGACY)
    monkeypatch.setenv("INTERNAL_API_KEY_CONSOLE_TO_VAULT", CONSOLE_KEY)
    monkeypatch.setenv("INTERNAL_API_KEY_MCP_INFRA_TO_VAULT", MCP_KEY)
    monkeypatch.setenv("INTERNAL_API_KEY_WORKSPACE_TO_VAULT", "workspace_to_vault_key_64_chars_zzzzzzzzzzzzzzzzzzzzzzzz")
    monkeypatch.setenv("INTERNAL_API_KEY_REFINEMENT_TO_VAULT", "refinement_to_vault_key_64_chars_zzzzzzzzzzzzzzzzzzzz")
    monkeypatch.setitem(sys.modules, "psycopg2", _module())
    monkeypatch.setitem(sys.modules, "yaml", _module(safe_load=lambda *a, **kw: {}))
    monkeypatch.setitem(
        sys.modules,
        "app.security",
        _module(get_internal_api_key=lambda: LEGACY),
    )
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    main = importlib.import_module("app.main")

    with pytest.raises(HTTPException) as exc:
        main.verify_api_key(x_api_key=LEGACY, x_internal_service="console")

    assert exc.value.status_code == 403
    main.verify_api_key(x_api_key=CONSOLE_KEY, x_internal_service="console")
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]


def test_wrong_key_rejected(vault_main):
    with pytest.raises(HTTPException) as exc:
        vault_main.verify_api_key(x_api_key="garbage", x_internal_service="console")
    assert exc.value.status_code == 403


def test_missing_service_rejected(vault_main):
    with pytest.raises(HTTPException) as exc:
        vault_main.verify_api_key(x_api_key=LEGACY, x_internal_service=None)
    assert exc.value.status_code == 403
