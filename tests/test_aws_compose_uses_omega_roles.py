"""Sprint v1.32 — AWS compose must not run app services as postgres superuser."""
from __future__ import annotations

from pathlib import Path

import yaml


COMPOSE = Path(__file__).resolve().parents[1] / "infra/terraform/deploy/docker-compose.aws.yml"


def test_aws_compose_app_database_urls_use_omega_roles():
    doc = yaml.safe_load(COMPOSE.read_text())
    env = {name: svc.get("environment", {}) for name, svc in doc["services"].items()}

    assert "omega_console:" in env["console"]["DATABASE_URL"]
    assert "omega_workspace:" in env["workspace"]["DATABASE_URL"]
    assert "omega_refinement:" in env["refinement"]["DATABASE_URL"]
    assert "omega_refinement_gold:" in env["refinement"]["GOLD_DATABASE_URL"]
    assert "omega_vault:" in env["vault"]["DATABASE_URL"]

    for service in ("console", "workspace", "refinement", "vault"):
        assert "://postgres:" not in env[service]["DATABASE_URL"], service
    assert "://postgres:" not in env["refinement"]["GOLD_DATABASE_URL"]
    assert "OMEGA_REFINEMENT_GOLD_PASSWORD" in env["refinement"]["GOLD_DATABASE_URL"]


def test_aws_compose_mcp_infra_uses_omega_role():
    doc = yaml.safe_load(COMPOSE.read_text())
    env = doc["services"]["mcp-infra"]["environment"]

    assert env["PG_USER"] == "omega_mcp_infra"
    assert "OMEGA_MCP_INFRA_PASSWORD" in env["PG_PASSWORD"]


def test_aws_postgres_bootstrap_receives_omega_passwords():
    doc = yaml.safe_load(COMPOSE.read_text())
    pgoptions = doc["services"]["postgres"]["environment"]["PGOPTIONS"]
    gold_pgoptions = doc["services"]["postgres_gold"]["environment"]["PGOPTIONS"]

    for setting in (
        "app.omega_console_password",
        "app.omega_workspace_password",
        "app.omega_refinement_password",
        "app.omega_vault_password",
        "app.omega_mcp_infra_password",
    ):
        assert setting in pgoptions
    assert "app.omega_refinement_gold_password" in gold_pgoptions
