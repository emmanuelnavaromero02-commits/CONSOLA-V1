"""Release-stack contracts for the Airflow health endpoint split."""

import os
import subprocess

from pathlib import Path

import yaml


REPO = Path(__file__).resolve().parents[1]
RELEASE_IMAGE_ENV = {
    f"OMEGA_GCP_IMAGE_{service.upper().replace('-', '_')}": "test.invalid/image@sha256:"
    + "0" * 64
    for service in (
        "console",
        "workspace",
        "refinement",
        "vault",
        "mcp-infra",
        "airflow",
        "replicon",
        "hubspot",
        "banxico",
        "inegi",
        "sec-edgar",
        "sap-hcm",
        "sap-s4hana",
        "sap-successfactors",
        "salesforce",
        "sap-b1",
    )
}


def _compose() -> dict:
    return yaml.safe_load((REPO / "infra/docker-compose.yml").read_text())


def _render_airflow(*overlays: str) -> dict:
    command = [
        "docker",
        "compose",
        "--env-file",
        "infra/.env.example",
        "-f",
        "infra/docker-compose.yml",
    ]
    for overlay in overlays:
        command.extend(("-f", overlay))
    command.extend(("--profile", "sap", "config"))
    clean_env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("COMPOSE_")
    }
    rendered = subprocess.run(
        command,
        cwd=REPO,
        env={**clean_env, **RELEASE_IMAGE_ENV},
        text=True,
        capture_output=True,
        check=True,
    )
    return yaml.safe_load(rendered.stdout)["services"]["airflow"]


def test_local_release_airflow_healthcheck_uses_real_root_endpoint() -> None:
    airflow = _compose()["services"]["airflow"]
    assert "AIRFLOW__WEBSERVER__BASE_URL" not in airflow.get("environment", {})
    assert airflow["healthcheck"]["test"] == [
        "CMD-SHELL",
        "curl -f http://127.0.0.1:8080/health || exit 1",
    ]


def test_exact_base_and_digest_release_renders_keep_root_health_endpoint() -> None:
    base = _render_airflow()
    digest_release = _render_airflow(
        "infra/docker-compose.dev.yml",
        "infra/terraform-gcp/release/docker-compose.release.yml",
    )
    for airflow in (base, digest_release):
        assert "AIRFLOW__WEBSERVER__BASE_URL" not in airflow.get("environment", {})
        assert airflow["healthcheck"]["test"] == [
            "CMD-SHELL",
            "curl -f http://127.0.0.1:8080/health || exit 1",
        ]
    assert "build" in base
    assert "build" not in digest_release
    assert digest_release["image"] == RELEASE_IMAGE_ENV["OMEGA_GCP_IMAGE_AIRFLOW"]


def test_gcp_overlay_keeps_prefixed_endpoint_with_matching_base_url() -> None:
    template = (
        REPO / "infra/terraform-gcp/templates/docker-compose.gcp.yml.tftpl"
    ).read_text()
    assert "AIRFLOW__WEBSERVER__BASE_URL" in template
    assert "AIRFLOW_HEALTH_PATH: $${AIRFLOW_HEALTH_PATH:-/airflow/health}" in template


def test_aws_overlay_keeps_prefixed_endpoint_with_matching_base_url() -> None:
    aws = yaml.safe_load(
        (REPO / "infra/terraform/deploy/docker-compose.aws.yml").read_text()
    )["services"]["airflow"]
    environment = aws["environment"]
    assert "AIRFLOW__WEBSERVER__BASE_URL" in environment
    assert environment["AIRFLOW_HEALTH_PATH"] == (
        "${AIRFLOW_HEALTH_PATH:-/airflow/health}"
    )
    assert aws["healthcheck"]["test"] == [
        "CMD-SHELL",
        "curl -f http://127.0.0.1:8080$${AIRFLOW_HEALTH_PATH:-/health} || exit 1",
    ]


def test_release_failure_logger_handles_each_container_id_separately() -> None:
    workflow = (REPO / ".github/workflows/release.yml").read_text()
    broken = "docker ps -aq | xargs -r docker logs --tail 120 2>&1 || true"
    assert broken not in workflow
    assert 'docker logs --tail 120 "${container_id}"' in workflow
