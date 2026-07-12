from __future__ import annotations

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]


def _yaml(path: str) -> dict:
    return yaml.safe_load((REPO / path).read_text(encoding="utf-8"))


def test_sec_edgar_local_compose_service_is_declared():
    svc = _yaml("infra/docker-compose.yml")["services"]["sec-edgar"]
    env = svc["environment"]

    assert svc["build"]["context"] == ".."
    assert svc["build"]["dockerfile"] == "cartridges/sec_edgar/Dockerfile"
    assert svc["container_name"] == "mode_sec_edgar"
    assert "8217:8217" in svc["ports"]
    assert env["INTERNAL_API_KEY_SEC_EDGAR_TO_CONSOLE"] == "${INTERNAL_API_KEY_SEC_EDGAR_TO_CONSOLE:-}"
    assert env["CONSOLE_URL"] == "http://console:8000"
    assert "omega_cartridge_sec_edgar" in env["DATABASE_URL"]
    assert "SEC_EDGAR_USER_AGENT" in env


def test_sec_edgar_aws_overlay_service_is_internal():
    svc = _yaml("infra/terraform/deploy/docker-compose.cartridges.yml")["services"]["sec-edgar"]
    env = svc["environment"]

    assert svc["image"].endswith("/sec_edgar:${IMAGE_TAG:?IMAGE_TAG is required}")
    assert "ports" not in svc
    assert env["INTERNAL_API_KEY_SEC_EDGAR_TO_CONSOLE"] == (
        "${INTERNAL_API_KEY_SEC_EDGAR_TO_CONSOLE:?INTERNAL_API_KEY_SEC_EDGAR_TO_CONSOLE is required}"
    )
    assert env["CONSOLE_URL"] == "http://console:8000"
    assert "omega_cartridge_sec_edgar" in env["DATABASE_URL"]
    assert "OMEGA_CARTRIDGE_SEC_EDGAR_PASSWORD" in env["DATABASE_URL"]
    assert "SEC_EDGAR_USER_AGENT" in env


def test_sec_edgar_seed_has_no_downstream_engines():
    src = (REPO / "infra/init/95_sec_edgar_role_and_seed.sql").read_text(encoding="utf-8")
    assert "omega_cartridge_sec_edgar" in src
    assert "sec_edgar_extract" in src
    for forbidden in ("mcp_servers", "monte carlo", "bayes"):
        assert forbidden not in src.lower()


def test_sec_edgar_airflow_runtime_wiring_is_declared():
    local = (REPO / "infra/docker-compose.yml").read_text(encoding="utf-8")
    aws = (REPO / "infra/terraform/deploy/docker-compose.aws.yml").read_text(encoding="utf-8")
    overlay = (REPO / "infra/terraform/deploy/docker-compose.cartridges.yml").read_text(encoding="utf-8")
    build = (REPO / "infra/terraform/deploy/build.sh").read_text(encoding="utf-8")

    assert "SEC_EDGAR_URL" in local
    assert "http://sec-edgar:8217" in local
    assert "cartridges/sec_edgar/dags" in local
    assert "SEC_EDGAR_URL" in aws
    assert "http://sec-edgar:8217" in aws
    assert "sec-edgar:" in overlay
    assert "banxico inegi sec-edgar sap-hcm" in build


def test_sec_edgar_release_image_and_deploy_paths_are_declared():
    release = (REPO / ".github/workflows/release.yml").read_text(encoding="utf-8")
    deploy = (REPO / "scripts/deploy_main_aws.py").read_text(encoding="utf-8")

    assert "service: sec_edgar" in release
    assert "dockerfile: ./cartridges/sec_edgar/Dockerfile" in release
    assert "banxico inegi sec-edgar sap-hcm" in deploy


def test_service_deploy_can_skip_dirty_host_worktree_checkout():
    update = (REPO / "infra/terraform/deploy/update.sh").read_text(encoding="utf-8")
    workflow = (REPO / ".github/workflows/deploy-aws.yml").read_text(encoding="utf-8")

    assert "OMEGA_SKIP_WORKTREE_UPDATE" in update
    assert "sudo OMEGA_SKIP_WORKTREE_UPDATE=1 bash" in workflow


def test_sec_edgar_runtime_secrets_are_bootstrapped_and_migrated():
    secret_key = "INTERNAL_API_KEY_SEC_EDGAR_TO_CONSOLE"
    role_key = "OMEGA_CARTRIDGE_SEC_EDGAR_PASSWORD"
    guc = "app.omega_cartridge_sec_edgar_password"
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


def test_sec_edgar_runtime_role_repair_fails_closed():
    src = (REPO / "infra/init/99zw_sec_edgar_runtime_role_repair.sql").read_text(encoding="utf-8")
    assert "omega_cartridge_sec_edgar" in src
    assert "RAISE EXCEPTION" in src
    assert "CREATE ROLE omega_cartridge_sec_edgar NOLOGIN" not in src
    assert "schema_migrations" in src
