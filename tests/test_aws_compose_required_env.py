"""Per-service required env keys must survive the env_file removal.

When the shared .env env_file was dropped, each service kept only what its
`environment:` block declares. A key a service's code needs but nobody declares
now vanishes silently -- `docker compose config` stays green and the container
boots, then fails on a later request (403, empty CORS, admin/admin, unbounded
DuckDB). This test renders the real compose and asserts each service still
resolves the keys its code requires, per the 2026-09-22 code audit.

The list is the audit's required_hard + required_silent, minus keys that reach
the service embedded in a DSN (checked by value, not key) and the console
evidence keys (private env_file). It skips without docker and runs in the
control-room-postgres-rls CI job. It is a static gate; the live per-service
rehearsal on a disposable instance is what exercises each key with a request.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DEPLOY = REPO / "infra/terraform/deploy"

# Keys each service must resolve as a standalone environment entry after the
# split. Derived from the per-service code audit (required_hard + silent that
# are not DSN-embedded). Kept explicit and readable on purpose: this is the
# contract, and a reviewer should see exactly what each service is promised.
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
    "airflow-scheduler": ["AIRFLOW_ADMIN_USER", "AIRFLOW_ADMIN_PASSWORD", "AWS_REGION"],
}


def _rendered_services() -> dict:
    # Deliberately not guarded by a docker skipif, matching
    # tests/test_mcp_infra_pdf_compose_capacity.py. This is the contract that
    # keeps the shared .env from reaching ~20 services; one that can skip
    # itself is how that guarantee goes missing without anyone noticing.
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
