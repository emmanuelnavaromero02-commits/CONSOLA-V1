from __future__ import annotations

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]


def _yaml(path: str) -> dict:
    return yaml.safe_load((REPO / path).read_text(encoding="utf-8"))


def test_inegi_local_compose_service_is_vault_backed():
    svc = _yaml("infra/docker-compose.yml")["services"]["inegi"]
    assert svc["build"]["context"] == ".."
    assert svc["build"]["dockerfile"] == "cartridges/inegi/Dockerfile"
    assert svc["container_name"] == "mode_inegi"
    assert "8216:8216" in svc["ports"]
    assert "INEGI_API_TOKEN" not in svc["environment"]
    assert svc["environment"]["INTERNAL_API_KEY_INEGI_TO_CONSOLE"] == "${INTERNAL_API_KEY_INEGI_TO_CONSOLE:-}"
    assert svc["environment"]["CONSOLE_URL"] == "http://console:8000"
    assert "omega_cartridge_inegi" in svc["environment"]["DATABASE_URL"]


def test_inegi_aws_overlay_service_is_internal_and_vault_backed():
    svc = _yaml("infra/terraform/deploy/docker-compose.cartridges.yml")["services"]["inegi"]
    env = svc["environment"]

    assert svc["image"].endswith("/inegi:${IMAGE_TAG:?IMAGE_TAG is required}")
    assert "ports" not in svc
    assert "INEGI_API_TOKEN" not in env
    assert env["INTERNAL_API_KEY_INEGI_TO_CONSOLE"] == (
        "${INTERNAL_API_KEY_INEGI_TO_CONSOLE:?INTERNAL_API_KEY_INEGI_TO_CONSOLE is required}"
    )
    assert env["CONSOLE_URL"] == "http://console:8000"
    assert "omega_cartridge_inegi" in env["DATABASE_URL"]
    assert "OMEGA_CARTRIDGE_INEGI_PASSWORD" in env["DATABASE_URL"]


def test_inegi_seed_has_no_downstream_engines():
    src = (REPO / "infra/init/95_inegi_role_and_seed.sql").read_text(encoding="utf-8")
    assert "omega_cartridge_inegi" in src
    assert "inegi_extract" in src
    for forbidden in ("mcp_servers", "dataset_refresh_chain", "monte carlo", "bayes"):
        assert forbidden not in src.lower()


def test_inegi_airflow_runtime_wiring_is_declared():
    local = (REPO / "infra/docker-compose.yml").read_text(encoding="utf-8")
    aws = (REPO / "infra/terraform/deploy/docker-compose.aws.yml").read_text(encoding="utf-8")
    overlay = (REPO / "infra/terraform/deploy/docker-compose.cartridges.yml").read_text(encoding="utf-8")
    build = (REPO / "infra/terraform/deploy/build.sh").read_text(encoding="utf-8")

    assert "INEGI_URL" in local
    assert "http://inegi:8216" in local
    assert "cartridges/inegi/dags" in local
    assert "INEGI_URL" in aws
    assert "http://inegi:8216" in aws
    assert "inegi:" in overlay
    assert "banxico inegi sap-hcm" in build


def test_inegi_runtime_secrets_are_bootstrapped_and_migrated():
    secret_key = "INTERNAL_API_KEY_INEGI_TO_CONSOLE"
    role_key = "OMEGA_CARTRIDGE_INEGI_PASSWORD"
    guc = "app.omega_cartridge_inegi_password"
    paths = [
        REPO / "infra/bootstrap-keys.sh",
        REPO / "scripts/aws-entrypoint.sh",
        REPO / "infra/terraform/infra/secretsmanager.tf",
        REPO / "infra/terraform/deploy/.env.example",
        REPO / "infra/.env.example",
    ]
    for path in paths:
        src = path.read_text(encoding="utf-8")
        assert secret_key in src
        assert role_key in src
    for path in [
        REPO / "scripts/apply_db_migrations.sh",
        REPO / "infra/terraform/deploy/apply_db_migrations.sh",
        REPO / "scripts/reconcile_db_passwords.sh",
        REPO / "infra/terraform/deploy/docker-compose.aws.yml",
    ]:
        assert guc in path.read_text(encoding="utf-8")


def test_inegi_runtime_role_repair_fails_closed():
    src = (REPO / "infra/init/99zt_inegi_runtime_role_repair.sql").read_text(encoding="utf-8")
    assert "omega_cartridge_inegi" in src
    assert "RAISE EXCEPTION" in src
    assert "CREATE ROLE omega_cartridge_inegi NOLOGIN" not in src
    assert "schema_migrations" in src
