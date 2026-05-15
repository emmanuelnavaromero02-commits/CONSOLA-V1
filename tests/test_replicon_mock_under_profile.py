"""Sprint v1.40 — replicon-mock service was removed entirely.

The mock served synthetic Replicon data for local development. Sprint
v1.21 (F3) had gated it behind ``profiles: [dev]`` to keep ``make up``
from starting it accidentally in production. v1.40 went further:
the mock is gone, the cartridge talks to real Replicon (sandbox or
prod URL configured per-environment in OMEGA Vault), and Vault-backed
credentials make per-tenant isolation explicit.

These tests now lock the *absence* of the mock — they fail loudly if
a future commit re-adds it under any profile.
"""
from __future__ import annotations

from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
COMPOSE = REPO_ROOT / "infra" / "docker-compose.yml"
MOCK_DIR = REPO_ROOT / "infra" / "replicon-mock"


def _compose_doc():
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def test_replicon_mock_service_does_not_exist():
    doc = _compose_doc()
    assert "replicon-mock" not in doc["services"], (
        "replicon-mock was removed in v1.40 (real Replicon credentials "
        "live in OMEGA Vault). Re-adding the mock would risk a "
        "misconfigured REPLICON_BASE_URL feeding synthetic data into "
        "real ETL pipelines."
    )


def test_replicon_mock_directory_does_not_exist():
    assert not MOCK_DIR.exists(), (
        f"{MOCK_DIR.relative_to(REPO_ROOT)} was deleted in v1.40 — "
        f"do not bring it back."
    )


def test_no_service_hard_depends_on_replicon_mock():
    """Even if a future commit re-adds the mock, no other service
    should depend on it. v1.21 (F3) removed three Airflow
    dependencies on the mock; v1.40 keeps that contract."""
    doc = _compose_doc()
    for name, svc in doc["services"].items():
        deps = svc.get("depends_on") or {}
        targets = deps if isinstance(deps, list) else list(deps.keys()) if isinstance(deps, dict) else []
        assert "replicon-mock" not in targets, (
            f"{name} depends_on replicon-mock — must not"
        )
