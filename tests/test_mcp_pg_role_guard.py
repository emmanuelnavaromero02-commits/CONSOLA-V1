from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

import psycopg2
import pytest


REPO = Path(__file__).resolve().parents[1]
MCP_ROLE_PASSWORD = os.environ.get("OMEGA_TEST_MCP_INFRA_PASSWORD", "wm-test-dummy-password")


def _dsn() -> str:
    return (os.environ.get("DATABASE_URL") or "").replace("postgresql+psycopg2://", "postgresql://")


def _db_reachable() -> bool:
    try:
        conn = psycopg2.connect(_dsn(), connect_timeout=2)
        conn.close()
        return True
    except Exception:
        return False


def _main_module(monkeypatch):
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    monkeypatch.setenv("AIRFLOW_USER", "airflow")
    monkeypatch.setenv("AIRFLOW_PASSWORD", "airflow")
    monkeypatch.setenv("PG_PASSWORD", "postgres")
    monkeypatch.setenv("SUPERSET_USER", "admin")
    monkeypatch.setenv("SUPERSET_PASSWORD", "admin")
    monkeypatch.setenv("INTERNAL_API_KEY", "test-internal-key-aaaaaaaaaaaaaaaaaaaaaaaa")
    monkeypatch.setenv("SECURITY_CONTEXT_SIGNING_KEY", "test-security-context-signing-key-0123456789")
    monkeypatch.syspath_prepend(str(REPO / "mcp-infra"))
    return importlib.import_module("app.main")


def test_default_pg_user_is_not_superuser(monkeypatch):
    module = _main_module(monkeypatch)
    config = importlib.import_module("app.config")
    field = config.Settings.model_fields["pg_user"]
    assert field.default == "omega_mcp_infra"
    assert module is not None


def test_guard_skips_when_database_unreachable(monkeypatch):
    module = _main_module(monkeypatch)
    monkeypatch.setattr(module.settings, "pg_host", "127.0.0.1")
    monkeypatch.setattr(module.settings, "pg_port", 1)
    monkeypatch.setattr(module.settings, "pg_user", "postgres")
    monkeypatch.setattr(module.settings, "pg_password", "irrelevant")
    module._assert_pg_role_not_privileged()


@pytest.mark.skipif(not _db_reachable(), reason="requires a reachable Postgres with the platform schema (DATABASE_URL)")
def test_guard_fails_fast_on_superuser_role(monkeypatch):
    module = _main_module(monkeypatch)
    parts = urlsplit(_dsn())
    monkeypatch.setattr(module.settings, "pg_host", parts.hostname or "localhost")
    monkeypatch.setattr(module.settings, "pg_port", parts.port or 5432)
    monkeypatch.setattr(module.settings, "pg_db", (parts.path or "/postgres").lstrip("/"))
    monkeypatch.setattr(module.settings, "pg_user", parts.username or "postgres")
    monkeypatch.setattr(module.settings, "pg_password", parts.password or "")

    with pytest.raises(RuntimeError) as error:
        module._assert_pg_role_not_privileged()
    assert "superuser" in str(error.value)


@pytest.mark.skipif(not _db_reachable(), reason="requires a reachable Postgres with the platform schema (DATABASE_URL)")
def test_guard_accepts_scoped_service_role(monkeypatch):
    module = _main_module(monkeypatch)
    parts = urlsplit(_dsn())
    monkeypatch.setattr(module.settings, "pg_host", parts.hostname or "localhost")
    monkeypatch.setattr(module.settings, "pg_port", parts.port or 5432)
    monkeypatch.setattr(module.settings, "pg_db", (parts.path or "/postgres").lstrip("/"))
    monkeypatch.setattr(module.settings, "pg_user", "omega_mcp_infra")
    monkeypatch.setattr(module.settings, "pg_password", MCP_ROLE_PASSWORD)

    module._assert_pg_role_not_privileged()
