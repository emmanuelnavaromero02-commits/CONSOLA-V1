from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DEPLOY = REPO / "infra/terraform/deploy"

REQUIRED = {
    "vault": ["VAULT_ENCRYPTION_KEY", "INTERNAL_API_KEY", "SECURITY_CONTEXT_SIGNING_KEY"],
    "console": [
        "ALLOWED_ORIGINS", "INTERNAL_API_KEY", "SECURITY_CONTEXT_SIGNING_KEY",
        "JWT_SECRET_KEY", "REDIS_URL",
        "INTERNAL_API_KEY_WORKSPACE_TO_CONSOLE", "INTERNAL_API_KEY_AIRFLOW_TO_CONSOLE",
        "INTERNAL_API_KEY_CONSOLE_TO_VAULT", "INTERNAL_API_KEY_CONSOLE_TO_REFINEMENT",
        "INTERNAL_API_KEY_CONSOLE_TO_MCP_INFRA", "INTERNAL_API_KEY_CONSOLE_TO_CARTRIDGE",
    ],
    "workspace": [
        "ALLOWED_ORIGINS", "INTERNAL_API_KEY", "SECURITY_CONTEXT_SIGNING_KEY",
        "INTERNAL_API_KEY_WORKSPACE_TO_REFINEMENT", "INTERNAL_API_KEY_WORKSPACE_TO_MCP_INFRA",
    ],
    "refinement": [
        "DUCKDB_MEMORY_LIMIT", "INTERNAL_API_KEY", "SECURITY_CONTEXT_SIGNING_KEY",
        "INTERNAL_API_KEY_CONSOLE_TO_REFINEMENT", "INTERNAL_API_KEY_WORKSPACE_TO_REFINEMENT",
        "INTERNAL_API_KEY_AIRFLOW_TO_REFINEMENT", "INTERNAL_API_KEY_CARTRIDGE_TO_REFINEMENT",
        "INTERNAL_API_KEY_REFINEMENT_TO_MCP_INFRA",
    ],
    "mcp-infra": [
        "INTERNAL_API_KEY", "SECURITY_CONTEXT_SIGNING_KEY",
        "INTERNAL_API_KEY_CONSOLE_TO_MCP_INFRA", "INTERNAL_API_KEY_WORKSPACE_TO_MCP_INFRA",
        "INTERNAL_API_KEY_AIRFLOW_TO_MCP_INFRA", "INTERNAL_API_KEY_REFINEMENT_TO_MCP_INFRA",
        "INTERNAL_API_KEY_MCP_INFRA_TO_VAULT", "SMTP_HOST",
    ],
    "airflow-init": ["AIRFLOW__CORE__FERNET_KEY", "AIRFLOW__WEBSERVER__SECRET_KEY"],
    "airflow": ["AIRFLOW__CORE__FERNET_KEY", "AIRFLOW__WEBSERVER__SECRET_KEY"],
    "airflow-scheduler": [
        "AIRFLOW_ADMIN_USER", "AIRFLOW_ADMIN_PASSWORD", "AWS_REGION",
        "AIRFLOW__CORE__FERNET_KEY",
    ],
}


def _rendered_services() -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        evidence = Path(tmp) / "evidence.env"
        evidence.write_text("", encoding="utf-8")
        env = os.environ.copy()
        env["AWS_ENV_FILE"] = str(DEPLOY / ".env.example")
        env["MODECISSIONS_CONTROL_ROOM_EVIDENCE_ENV_FILE"] = str(evidence)
        result = subprocess.run(
            [
                "docker", "compose",
                "--env-file", str(DEPLOY / ".env.example"),
                "-f", str(DEPLOY / "docker-compose.aws.yml"),
                "-f", str(DEPLOY / "docker-compose.cartridges.yml"),
                "config", "--format", "json",
            ],
            cwd=DEPLOY, env=env, text=True, capture_output=True, check=False,
        )
        if result.returncode != 0:
            pytest.fail(f"docker compose config failed: {result.stderr}")
        return json.loads(result.stdout)["services"]


@pytest.mark.parametrize("service", sorted(REQUIRED))
def test_service_resolves_its_required_env(service: str) -> None:
    services = _rendered_services()
    assert service in services, f"{service} missing from the rendered compose"
    present = (services[service].get("environment") or {})
    missing = [k for k in REQUIRED[service] if k not in present]
    assert not missing, (
        f"{service} no longer resolves required env keys {missing}. "
        "Removing the shared env_file dropped them; declare them in the "
        "service's environment block."
    )
