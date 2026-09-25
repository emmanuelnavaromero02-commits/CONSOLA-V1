from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
CART_DIR = REPO_ROOT / "cartridges" / "replicon"


SERVICE_PATH_MARKERS = (
    "/cartridges/sap_",
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


@pytest.fixture
def fresh_settings(monkeypatch):
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", "OENi0J3O2llg-_pAlcZNzewjjm-LpaaCWUYatHmCQpQ=")
    monkeypatch.setenv("MINIO_ACCESS_KEY", "test-minio-access")
    monkeypatch.setenv("MINIO_SECRET_KEY", "test-minio-secret")
    saved_path = list(sys.path)
    sys.path[:] = [
        p for p in sys.path
        if not any(marker in p for marker in SERVICE_PATH_MARKERS)
    ]
    sys.path.insert(0, str(CART_DIR))
    _purge_app_modules()

    def _load():
        _purge_app_modules()
        mod = importlib.import_module("app.core.config")
        return mod.Settings()

    yield _load

    sys.path[:] = saved_path
    _purge_app_modules()


def test_database_url_respects_env_override(fresh_settings, monkeypatch):
    target = (
        "postgresql+psycopg2://omega_cartridge_replicon:s3cret"
        "@postgres:5432/modecissions"
    )
    monkeypatch.setenv("DATABASE_URL", target)
    s = fresh_settings()
    assert s.database_url == target, (
        "settings.database_url must echo the DATABASE_URL env value verbatim"
    )
    assert "omega_cartridge_replicon" in s.database_url
    assert "s3cret" in s.database_url


def test_database_url_falls_back_to_pg_fields(fresh_settings, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("PG_USER", "omega_cartridge_replicon")
    monkeypatch.setenv("PG_PASSWORD", "pg-secret")
    s = fresh_settings()
    assert s.database_url.startswith("postgresql+psycopg2://"), (
        "fallback path must keep the SQLAlchemy driver hint for the "
        "engine in pg_client.py"
    )
    assert f"@{s.pg_host}:{s.pg_port}/{s.pg_db}" in s.database_url


def test_database_url_treats_empty_env_as_unset(fresh_settings, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "   ")
    monkeypatch.setenv("PG_USER", "omega_cartridge_replicon")
    monkeypatch.setenv("PG_PASSWORD", "pg-secret")
    s = fresh_settings()
    assert s.pg_user in s.database_url, (
        "empty DATABASE_URL must fall through to the pg_user fallback "
        "instead of writing ``@postgres:5432`` with a blank user"
    )


def test_asyncpg_dsn_strips_sqlalchemy_driver_hint(fresh_settings, monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg2://omega_cartridge_replicon:x@postgres:5432/db",
    )
    s = fresh_settings()
    dsn = s.asyncpg_dsn
    assert dsn.startswith("postgresql://"), (
        f"asyncpg_dsn must start with postgresql:// — got {dsn!r}"
    )
    assert "+psycopg2" not in dsn


def test_asyncpg_dsn_on_fallback_path_also_strips_hint(fresh_settings, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("PG_USER", "omega_cartridge_replicon")
    monkeypatch.setenv("PG_PASSWORD", "pg-secret")
    s = fresh_settings()
    assert s.asyncpg_dsn.startswith("postgresql://")
    assert "+psycopg2" not in s.asyncpg_dsn


def test_gold_database_url_respects_env_override(fresh_settings, monkeypatch):
    monkeypatch.setenv(
        "GOLD_DATABASE_URL",
        "postgresql+psycopg2://omega_refinement_gold:gpw@postgres_gold:5433/gold",
    )
    s = fresh_settings()
    assert "omega_refinement_gold" in s.gold_database_url


def test_pg_client_get_connection_uses_settings_dsn():
    src = (CART_DIR / "app" / "core" / "pg_client.py").read_text(encoding="utf-8")
    assert "settings.database_url" in src, (
        "pg_client.get_connection must consult settings.database_url "
        "so DATABASE_URL env overrides apply"
    )
    func_block = src[src.index("def get_connection"):src.index("engine = create_engine")]
    assert "host=settings.pg_host" not in func_block, (
        "pg_client.get_connection still passes individual pg_* fields — "
        "those silently fall back to postgres:postgres defaults"
    )


def test_job_runner_uses_settings_asyncpg_dsn():
    src = (CART_DIR / "app" / "core" / "job_runner.py").read_text(encoding="utf-8")
    assert "settings.asyncpg_dsn" in src, (
        "job_runner._get_pool must call asyncpg.create_pool("
        "settings.asyncpg_dsn) so the DATABASE_URL override is honored"
    )
