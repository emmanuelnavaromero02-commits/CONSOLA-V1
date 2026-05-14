"""Sprint v1.32.1 — Vault MCP mutating tools must be dev-only."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest


SERVICE_PATH_MARKERS = (
    "/cartridges/",
    "/console",
    "/mcp-infra",
    "/refinement",
    "/vault",
    "/workspace",
)


def _purge_app_modules() -> None:
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]


@pytest.fixture(autouse=True)
def clean_app_modules_after():
    yield
    _purge_app_modules()


def _load_vault_tools(monkeypatch):
    _purge_app_modules()
    root = Path(__file__).resolve().parents[1]
    sys.path[:] = [
        p for p in sys.path
        if not any(marker in p for marker in SERVICE_PATH_MARKERS)
    ]
    sys.path.insert(0, str(root / "mcp-infra"))
    monkeypatch.setenv("AIRFLOW_USER", "airflow")
    monkeypatch.setenv("AIRFLOW_PASSWORD", "airflow")
    monkeypatch.setenv("PG_PASSWORD", "pg-password")
    monkeypatch.setenv("SUPERSET_USER", "admin")
    monkeypatch.setenv("SUPERSET_PASSWORD", "admin-password")
    return importlib.import_module("app.tools.vault")


@pytest.mark.parametrize(
    ("func_name", "kwargs"),
    [
        (
            "vault_set_connection",
            {
                "cartridge_id": "sap_hcm",
                "conn_id": "default",
                "base_url": "https://api.example",
                "auth_method": "bearer_token",
                "token": "secret",
            },
        ),
        ("vault_delete_connection", {"cartridge_id": "sap_hcm", "conn_id": "default"}),
        ("vault_set_secret", {"scope": "sap_hcm", "key": "TOKEN", "value": "secret"}),
    ],
)
@pytest.mark.asyncio
async def test_vault_mutating_tools_disabled_in_production(monkeypatch, func_name, kwargs):
    vault = _load_vault_tools(monkeypatch)
    monkeypatch.setenv("APP_ENV", "production")

    with pytest.raises(PermissionError, match=f"{func_name} is disabled outside development"):
        await getattr(vault, func_name)(**kwargs)
