from __future__ import annotations

from pathlib import Path

import yaml


REPO = Path(__file__).resolve().parents[1]


def _yaml(path: str) -> dict:
    return yaml.safe_load((REPO / path).read_text(encoding="utf-8"))


def test_hubspot_local_compose_service_is_executable():
    compose = _yaml("infra/docker-compose.yml")
    services = compose["services"]
    svc = services["hubspot"]
    assert svc["build"]["context"] == "../cartridges/hubspot"
    assert svc["container_name"] == "mode_hubspot"
    assert "8210:8210" in svc["ports"]
    env = svc["environment"]
    assert "omega_cartridge_hubspot" in env["DATABASE_URL"]
    assert env["HUBSPOT_BASE_URL"] == "${HUBSPOT_BASE_URL:-https://api.hubapi.com}"
    assert env["HUBSPOT_API_TOKEN"] == "${HUBSPOT_API_TOKEN:-}"
    assert "INTERNAL_API_KEY_HUBSPOT_TO_CONSOLE" in env
    assert "8210/health" in " ".join(svc["healthcheck"]["test"])
    assert "app.omega_cartridge_hubspot_password" in services["postgres"]["environment"]["PGOPTIONS"]


def test_hubspot_airflow_wiring_exists_locally_and_in_aws():
    local = (REPO / "infra/docker-compose.yml").read_text(encoding="utf-8")
    aws = (REPO / "infra/terraform/deploy/docker-compose.aws.yml").read_text(encoding="utf-8")
    for src in (local, aws):
        assert src.count("HUBSPOT_URL") >= 2
        assert "cartridges/hubspot/dags" in src
        assert "http://hubspot:8210" in src


def test_hubspot_aws_overlay_ships_release_image():
    overlay = _yaml("infra/terraform/deploy/docker-compose.cartridges.yml")
    svc = overlay["services"]["hubspot"]
    assert svc["image"] == "ghcr.io/${GHCR_OWNER:-emmanuelnavaromero02-commits}/hubspot:${IMAGE_TAG:?IMAGE_TAG is required}"
    env = svc["environment"]
    assert "omega_cartridge_hubspot" in env["DATABASE_URL"]
    assert "INTERNAL_API_KEY_HUBSPOT_TO_CONSOLE" in env
    assert "8210/health" in " ".join(svc["healthcheck"]["test"])


def test_hubspot_role_and_seed_migration_closes_runtime_contract():
    src = (REPO / "infra/init/93_hubspot_role_and_seed.sql").read_text(encoding="utf-8")
    required = [
        "omega_cartridge_hubspot",
        "omega_cartridge_jobs_owner",
        "app.omega_cartridge_hubspot_password",
        "INSERT INTO cartridges",
        "'hubspot'",
        "INSERT INTO marketplace_products",
        "INSERT INTO cartridge_dags",
        "hubspot_extract",
        "hubspot_extract_all",
        "forecast_watchdog",
        "stale_deal_chaser",
        "REVOKE ALL PRIVILEGES",
        "vault_entries",
    ]
    for needle in required:
        assert needle in src


def test_hubspot_refinement_datasets_are_seeded():
    src = (REPO / "infra/init/94_hubspot_datasets_seed.sql").read_text(encoding="utf-8")
    for dataset in [
        "hubspot_deals_latest",
        "hubspot_owners_latest",
        "hubspot_pipelines_latest",
        "pipeline_salud",
        "forecast_mensual",
        "revenue_por_vendedor",
        "conversion_por_etapa",
        "deals_estancados",
    ]:
        assert dataset in src
    assert "raw/hubspot/deals" in src
    assert "silver/hubspot/hubspot_deals_latest" in src
    assert "gold/hubspot/pipeline_salud" in src


def test_hubspot_refresh_by_source_uses_trusted_security_context():
    job_runner = (REPO / "cartridges/hubspot/app/core/job_runner.py").read_text(encoding="utf-8")
    request_context = (REPO / "cartridges/hubspot/app/core/request_context.py").read_text(encoding="utf-8")
    assert '"x-internal-service": "cartridge-hubspot"' in job_runner
    assert '"security_context": refinement_security_context(security_context)' in job_runner
    assert 'source": "cartridge-hubspot"' in request_context
    assert 'permissions": ["datasets.read", "datasets.write"]' in request_context
    assert 'allowed_cartridges": ["hubspot"]' in request_context
    assert '"raw/hubspot/"' in request_context


def test_hubspot_extraction_preserves_console_workspace_scope():
    main = (REPO / "cartridges/hubspot/app/main.py").read_text(encoding="utf-8")
    job_runner = (REPO / "cartridges/hubspot/app/core/job_runner.py").read_text(encoding="utf-8")
    extraction = (REPO / "cartridges/hubspot/app/services/extraction_service.py").read_text(encoding="utf-8")
    parquet = (REPO / "cartridges/hubspot/app/services/parquet_service.py").read_text(encoding="utf-8")
    mcp = (REPO / "cartridges/hubspot/app/mcp_server.py").read_text(encoding="utf-8")
    extract_all_dag = (REPO / "cartridges/hubspot/dags/hubspot_extract_all.py").read_text(encoding="utf-8")
    assert "set_security_context(body.get(\"security_context\"))" in main
    assert 'config = {**config, "security_context": security_context}' in job_runner
    assert 'overridden["security_context"] = security_context' in job_runner
    assert 'security_context=security_context' in extraction
    assert 'f"raw/hubspot/{entity}/{scope}"' in parquet
    assert "scope = scoped_prefix()" in mcp
    assert "skill_body = {" in extract_all_dag
    assert 'for key in ("tenant_id", "workspace_id", "security_context")' in extract_all_dag
    assert "json=skill_body" in extract_all_dag


def test_refinement_accepts_hubspot_internal_origin():
    refinement = (REPO / "refinement/app/main.py").read_text(encoding="utf-8")
    assert '"hubspot":              "INTERNAL_API_KEY_CARTRIDGE_TO_REFINEMENT"' in refinement
    assert '"cartridge-hubspot":    "INTERNAL_API_KEY_CARTRIDGE_TO_REFINEMENT"' in refinement
    assert '"cartridge-hubspot": {"cartridge-hubspot", "hubspot"}' in refinement
    assert "source.startswith(\"cartridge-\")" in refinement
    assert "service_materializer" in refinement


def test_hubspot_docker_ci_and_release_publish_image():
    docker_ci = (REPO / ".github/workflows/docker-image.yml").read_text(encoding="utf-8")
    release = (REPO / ".github/workflows/release.yml").read_text(encoding="utf-8")
    for src in (docker_ci, release):
        assert "service: hubspot" in src
        assert "context: ./cartridges/hubspot" in src
