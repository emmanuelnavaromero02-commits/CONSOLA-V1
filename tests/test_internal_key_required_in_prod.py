"""Sprint v1.32 — production must fail fast when per-pair keys are missing."""
from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

import pytest


LEGACY = "legacy_internal_key_with_more_than_thirty_two_characters"
SERVICE_PATH_MARKERS = (
    "/cartridges/",
    "/console",
    "/mcp-infra",
    "/refinement",
    "/vault",
    "/workspace",
)


def _module(**attrs):
    mod = types.ModuleType("stub")
    for key, value in attrs.items():
        setattr(mod, key, value)
    return mod


def _purge_app_modules() -> None:
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]


@pytest.fixture(autouse=True)
def clean_app_modules_after():
    yield
    _purge_app_modules()


def test_console_refuses_production_without_pair_keys(monkeypatch):
    _purge_app_modules()
    root = Path(__file__).resolve().parents[1]
    sys.path[:] = [
        p for p in sys.path
        if not any(marker in p for marker in SERVICE_PATH_MARKERS)
    ]
    sys.path.insert(0, str(root / "console"))
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("INTERNAL_API_KEY", LEGACY)
    monkeypatch.delenv("INTERNAL_API_KEY_WORKSPACE_TO_CONSOLE", raising=False)
    monkeypatch.delenv("INTERNAL_API_KEY_CARTRIDGE_TO_CONSOLE", raising=False)
    monkeypatch.setitem(sys.modules, "bcrypt", _module())
    monkeypatch.setitem(sys.modules, "asyncpg", _module())

    with pytest.raises(RuntimeError, match="missing per-pair internal API key"):
        importlib.import_module("app.services.auth")


def test_vault_refuses_production_without_pair_keys(monkeypatch):
    _purge_app_modules()
    root = Path(__file__).resolve().parents[1]
    sys.path[:] = [
        p for p in sys.path
        if not any(marker in p for marker in SERVICE_PATH_MARKERS)
    ]
    sys.path.insert(0, str(root / "vault"))
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("INTERNAL_API_KEY", LEGACY)
    for name in (
        "INTERNAL_API_KEY_CONSOLE_TO_VAULT",
        "INTERNAL_API_KEY_MCP_INFRA_TO_VAULT",
        "INTERNAL_API_KEY_WORKSPACE_TO_VAULT",
        "INTERNAL_API_KEY_REFINEMENT_TO_VAULT",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setitem(sys.modules, "psycopg2", _module())
    monkeypatch.setitem(sys.modules, "yaml", _module(safe_load=lambda *a, **kw: {}))
    monkeypatch.setitem(sys.modules, "app.security", _module(get_internal_api_key=lambda: LEGACY))

    with pytest.raises(RuntimeError, match="missing per-pair internal API key"):
        importlib.import_module("app.main")
