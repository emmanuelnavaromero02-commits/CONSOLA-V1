"""
Sprint v1.40.2 — Replicon cartridge respects DATABASE_URL env var.

Pre-v1.40.2 the cartridge's ``settings.database_url`` property built
the URL from the individual ``pg_user`` / ``pg_password`` /
``pg_host`` / ``pg_db`` fields, ignoring the ``DATABASE_URL`` env var
that ``infra/docker-compose.yml`` passes to the container (which
points at the v1.40 ``omega_cartridge_replicon`` role). The cartridge
silently tried to log in as the ``postgres`` superuser, hit
``asyncpg.exceptions.InvalidPasswordError``, and went into a restart
loop.

These tests pin the contract:

  * ``settings.database_url`` returns the env value when DATABASE_URL
    is set.
  * ``settings.asyncpg_dsn`` always emits ``postgresql://`` (asyncpg
    rejects the ``+psycopg2`` SQLAlchemy driver hint).
  * Dev / test runs without DATABASE_URL still fall back to the
    legacy pg_user/pg_password defaults so the ZIP-shipped behavior
    is preserved when the cartridge is run standalone.
  * ``pg_client.get_connection`` and ``job_runner._get_pool`` both
    route through the settings property so the env override applies
    uniformly.
"""
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
    """Reload ``app.core.config`` with a clean sys.path / sys.modules
    so each test sees a freshly-constructed ``Settings()`` instance."""
    # Replicon's services import yaml + the FIELD_ENCRYPTION_KEY guard
    # at module top — config.py itself doesn't touch them, but keep
    # the fixture forward-compatible.
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", "OENi0J3O2llg-_pAlcZNzewjjm-LpaaCWUYatHmCQpQ=")
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
        # Force a fresh Settings instance under the current env.
        return mod.Settings()

    yield _load

    sys.path[:] = saved_path
    _purge_app_modules()


# ── settings.database_url ──────────────────────────────────────────────────

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
    # Sanity: the role and password DID make it through (the v1.40
    # bug fell back to pg_user='postgres' / pg_password='postgres').
    assert "omega_cartridge_replicon" in s.database_url
    assert "s3cret" in s.database_url


def test_database_url_falls_back_to_pg_fields(fresh_settings, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    s = fresh_settings()
    # Legacy ZIP behavior: build the URL from pg_user / pg_password
    # defaults. Dev runs without DATABASE_URL must still get a usable
    # connection string.
    assert s.database_url.startswith("postgresql+psycopg2://"), (
        "fallback path must keep the SQLAlchemy driver hint for the "
        "engine in pg_client.py"
    )
    assert f"@{s.pg_host}:{s.pg_port}/{s.pg_db}" in s.database_url


def test_database_url_treats_empty_env_as_unset(fresh_settings, monkeypatch):
    """An empty / whitespace-only DATABASE_URL must NOT shadow the
    field-based fallback (mirrors the v1.39 whitespace guard pattern)."""
    monkeypatch.setenv("DATABASE_URL", "   ")
    s = fresh_settings()
    assert s.pg_user in s.database_url, (
        "empty DATABASE_URL must fall through to the pg_user fallback "
        "instead of writing ``@postgres:5432`` with a blank user"
    )


# ── settings.asyncpg_dsn ───────────────────────────────────────────────────

def test_asyncpg_dsn_strips_sqlalchemy_driver_hint(fresh_settings, monkeypatch):
    """asyncpg.create_pool() throws ``ValueError: Invalid scheme`` if
    given ``postgresql+psycopg2://...``. The settings property must
    return the clean ``postgresql://...`` form regardless of which
    source produced the URL."""
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
    s = fresh_settings()
    assert s.asyncpg_dsn.startswith("postgresql://")
    assert "+psycopg2" not in s.asyncpg_dsn


# ── gold_database_url honors GOLD_DATABASE_URL ─────────────────────────────

def test_gold_database_url_respects_env_override(fresh_settings, monkeypatch):
    monkeypatch.setenv(
        "GOLD_DATABASE_URL",
        "postgresql+psycopg2://omega_refinement_gold:gpw@postgres_gold:5433/gold",
    )
    s = fresh_settings()
    assert "omega_refinement_gold" in s.gold_database_url


# ── pg_client wires through settings.database_url ───────────────────────────

def test_pg_client_get_connection_uses_settings_dsn():
    """pg_client.get_connection must read the DSN from settings, not
    from the pg_host/pg_user fields directly. The v1.40 wiring bug
    was that it used the fields and silently fell back to the
    ``postgres`` superuser defaults."""
    src = (CART_DIR / "app" / "core" / "pg_client.py").read_text(encoding="utf-8")
    # New shape: psycopg2.connect(settings.database_url-derived dsn).
    assert "settings.database_url" in src, (
        "pg_client.get_connection must consult settings.database_url "
        "so DATABASE_URL env overrides apply"
    )
    # The legacy field-by-field signature must be gone.
    func_block = src[src.index("def get_connection"):src.index("engine = create_engine")]
    assert "host=settings.pg_host" not in func_block, (
        "pg_client.get_connection still passes individual pg_* fields — "
        "those silently fall back to postgres:postgres defaults"
    )


def test_job_runner_uses_settings_asyncpg_dsn():
    """job_runner.py's pool factory must use the shared
    ``settings.asyncpg_dsn`` property, not hand-roll its own
    ``+psycopg2`` strip — duplicating that logic was how the v1.40
    auth bug stayed hidden in a previous code review pass."""
    src = (CART_DIR / "app" / "core" / "job_runner.py").read_text(encoding="utf-8")
    assert "settings.asyncpg_dsn" in src, (
        "job_runner._get_pool must call asyncpg.create_pool("
        "settings.asyncpg_dsn) so the DATABASE_URL override is honored"
    )
