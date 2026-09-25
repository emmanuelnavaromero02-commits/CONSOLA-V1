from __future__ import annotations

from pathlib import Path

import yaml


COMPOSE = Path(__file__).resolve().parents[1] / "infra/docker-compose.yml"


def test_local_refinement_gold_database_url_uses_omega_role():
    doc = yaml.safe_load(COMPOSE.read_text())
    env = doc["services"]["refinement"]["environment"]

    assert "omega_refinement_gold:" in env["GOLD_DATABASE_URL"]
    assert "OMEGA_REFINEMENT_GOLD_PASSWORD" in env["GOLD_DATABASE_URL"]
    assert "://postgres:" not in env["GOLD_DATABASE_URL"]


def test_local_postgres_gold_receives_gold_role_password():
    doc = yaml.safe_load(COMPOSE.read_text())
    pgoptions = doc["services"]["postgres_gold"]["environment"]["PGOPTIONS"]

    assert "app.omega_refinement_gold_password" in pgoptions
