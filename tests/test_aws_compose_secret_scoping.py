"""Secrets must reach only the services that use them.

Until the per-service environment split, every service loaded the whole .env
through `env_file`, so POSTGRES_PASSWORD (the superuser), VAULT_ENCRYPTION_KEY
(the vault master key) and each role password sat in the environment of ~20
containers. A remote-code hole in any one of them handed over all of it.

These checks render the real compose with `docker compose config` and assert,
per service, that each sentinel value reaches only its allowlist -- including
when the value is embedded inside a DSN, which a key-name check would miss.
They skip without docker and run in the control-room-postgres-rls CI job.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DEPLOY = REPO / "infra/terraform/deploy"
AWS_COMPOSE = DEPLOY / "docker-compose.aws.yml"
CARTRIDGES_COMPOSE = DEPLOY / "docker-compose.cartridges.yml"

# Sentinel values injected through the shell, which outranks --env-file.
SENTINELS = {
    "POSTGRES_PASSWORD": "SENTINEL_SUPERUSER_PW",
    "VAULT_ENCRYPTION_KEY": "SENTINEL_VAULT_MASTER_KEY",
    "OMEGA_CONSOLE_PASSWORD": "SENTINEL_CONSOLE_ROLE_PW",
    "OMEGA_WORKSPACE_PASSWORD": "SENTINEL_WORKSPACE_ROLE_PW",
    "OMEGA_REFINEMENT_PASSWORD": "SENTINEL_REFINEMENT_ROLE_PW",
}

# Which services may legitimately see each sentinel, by any route (raw var or
# embedded in a DSN). Everything else must not.
ALLOWED = {
    # Only initdb (empty volume) and the two one-shot admin bootstraps connect
    # as the superuser.
    "POSTGRES_PASSWORD": {"postgres", "postgres_gold", "superset-init", "airflow-init"},
    # The vault master key belongs to vault alone.
    "VAULT_ENCRYPTION_KEY": {"vault"},
    # Each role password reaches only the service that connects as that role,
    # plus the postgres container (PGOPTIONS, for initdb) and the migration
    # path is host-side, not a service.
    "OMEGA_CONSOLE_PASSWORD": {"console", "postgres"},
    "OMEGA_WORKSPACE_PASSWORD": {"workspace", "postgres"},
    "OMEGA_REFINEMENT_PASSWORD": {"refinement", "postgres"},
}


def _merged_env_file(dest: Path) -> None:
    """Copy .env.example and overwrite the sentinel keys in place.

    The sentinels must live in the env file itself, not just the shell:
    `env_file` loads the file literally, so a shell override would only reach
    `${VAR}` interpolation and miss exactly the env_file copying this guards.
    """
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
    docker = shutil.which("docker")
    if not docker:
        pytest.skip("docker compose is required to render the compose files")
    with tempfile.TemporaryDirectory() as tmp:
        env_file = Path(tmp) / "sentinel.env"
        _merged_env_file(env_file)
        # console still loads a private per-service evidence file; it must exist
        # for `docker compose config` to resolve, but its contents are
        # irrelevant to secret scoping here.
        evidence = Path(tmp) / "evidence.env"
        evidence.write_text("", encoding="utf-8")
        env = os.environ.copy()
        for key in SENTINELS:
            env.pop(key, None)  # let the env file, not the shell, be the source
        env["AWS_ENV_FILE"] = str(env_file)
        env["MODECISSIONS_CONTROL_ROOM_EVIDENCE_ENV_FILE"] = str(evidence)
        result = subprocess.run(
            [
                docker, "compose",
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
    """Every service whose resolved environment contains the sentinel anywhere."""
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
    # Guard against a rename that would silently empty this test.
    assert consumers, f"{sentinel} reached no service; the sentinel wiring broke"


def test_env_file_is_not_the_shared_env_for_any_service() -> None:
    # The shared-.env env_file is the whole problem: it copies every secret into
    # every service. Once split, no service may load it.
    services = _rendered_services()
    offenders = []
    for name, service in services.items():
        for entry in service.get("env_file") or []:
            path = entry["path"] if isinstance(entry, dict) else entry
            base = os.path.basename(str(path))
            # The per-service evidence file for console is allowed; the shared
            # .env is not.
            if base == ".env" or base.endswith("/.env"):
                offenders.append(f"{name}:{path}")
    assert not offenders, (
        f"these services still load the shared .env via env_file: {offenders}"
    )
