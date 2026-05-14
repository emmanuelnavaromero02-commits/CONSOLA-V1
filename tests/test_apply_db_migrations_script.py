"""Sprint v1.32 — existing DB volumes need an explicit migration path."""
from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_makefile_exposes_migrate_target():
    makefile = (REPO_ROOT / "Makefile").read_text()

    assert ".PHONY: help up down nuke logs ps test smoke migrate rotate-keys" in makefile
    assert "migrate:" in makefile
    assert "scripts/apply_db_migrations.sh" in makefile


def test_apply_db_migrations_tracks_schema_migrations_and_pgoptions():
    script = (REPO_ROOT / "scripts/apply_db_migrations.sh").read_text()

    assert "schema_migrations" in script
    assert "/docker-entrypoint-initdb.d/${filename}" in script
    assert "docker compose -f" in script
    assert "PGOPTIONS=" in script
    assert "app.omega_vault_password" in script
