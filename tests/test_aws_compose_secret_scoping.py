from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DEPLOY = REPO / "infra/terraform/deploy"
AWS_COMPOSE = DEPLOY / "docker-compose.aws.yml"
CARTRIDGES_COMPOSE = DEPLOY / "docker-compose.cartridges.yml"

SENTINELS = {
    "POSTGRES_PASSWORD": "SENTINEL_SUPERUSER_PW",
    "VAULT_ENCRYPTION_KEY": "SENTINEL_VAULT_MASTER_KEY",
    "OMEGA_CONSOLE_PASSWORD": "SENTINEL_CONSOLE_ROLE_PW",
    "OMEGA_WORKSPACE_PASSWORD": "SENTINEL_WORKSPACE_ROLE_PW",
    "OMEGA_REFINEMENT_PASSWORD": "SENTINEL_REFINEMENT_ROLE_PW",
}

ALLOWED = {
    "POSTGRES_PASSWORD": {"postgres", "postgres_gold", "superset-init", "airflow-init"},
    "VAULT_ENCRYPTION_KEY": {"vault"},
    "OMEGA_CONSOLE_PASSWORD": {"console", "postgres"},
    "OMEGA_WORKSPACE_PASSWORD": {"workspace", "postgres"},
    "OMEGA_REFINEMENT_PASSWORD": {"refinement", "postgres"},
}


def _merged_env_file(dest: Path) -> None:
    lines = (DEPLOY / ".env.example").read_text(encoding="utf-8").splitlines()
    seen = set()
    out = []
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else None
        if key in SENTINELS:
            out.append(f"{key}={SENTINELS[key]}")
            seen.add(key)
        else:
            out.append(line)
    for key, value in SENTINELS.items():
        if key not in seen:
            out.append(f"{key}={value}")
    dest.write_text("\n".join(out) + "\n", encoding="utf-8")


def _rendered_services() -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        env_file = Path(tmp) / "sentinel.env"
        _merged_env_file(env_file)
        evidence = Path(tmp) / "evidence.env"
        evidence.write_text("", encoding="utf-8")
        env = os.environ.copy()
        for key in SENTINELS:
            env.pop(key, None)
        env["AWS_ENV_FILE"] = str(env_file)
        env["MODECISSIONS_CONTROL_ROOM_EVIDENCE_ENV_FILE"] = str(evidence)
        result = subprocess.run(
            [
                "docker", "compose",
                "--env-file", str(env_file),
                "-f", str(AWS_COMPOSE),
                "-f", str(CARTRIDGES_COMPOSE),
                "config", "--format", "json",
            ],
            cwd=DEPLOY, env=env, text=True, capture_output=True, check=False,
        )
        if result.returncode != 0:
            pytest.fail(f"docker compose config failed: {result.stderr}")
        return json.loads(result.stdout)["services"]


def _consumers(services: dict, sentinel: str) -> set[str]:
    found = set()
    for name, service in services.items():
        for value in (service.get("environment") or {}).values():
            if value is not None and sentinel in str(value):
                found.add(name)
                break
    return found


@pytest.mark.parametrize("var,sentinel", sorted(SENTINELS.items()))
def test_secret_reaches_only_its_allowlist(var: str, sentinel: str) -> None:
    services = _rendered_services()
    consumers = _consumers(services, sentinel)
    allowed = ALLOWED[var]
    leaked = consumers - allowed
    assert not leaked, (
        f"{var} reaches {sorted(leaked)}, which is outside its allowlist "
        f"{sorted(allowed)}. A per-service split must not expose it there."
    )
    assert consumers, f"{sentinel} reached no service; the sentinel wiring broke"


def test_env_file_is_not_the_shared_env_for_any_service() -> None:
    services = _rendered_services()
    offenders = []
    for name, service in services.items():
        for entry in service.get("env_file") or []:
            path = entry["path"] if isinstance(entry, dict) else entry
            base = os.path.basename(str(path))
            if base == ".env" or base.endswith("/.env"):
                offenders.append(f"{name}:{path}")
    assert not offenders, (
        f"these services still load the shared .env via env_file: {offenders}"
    )
