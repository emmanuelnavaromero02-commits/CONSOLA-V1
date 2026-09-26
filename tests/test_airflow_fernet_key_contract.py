from __future__ import annotations

import base64
import binascii
import os
import re
import subprocess
from pathlib import Path

import yaml


REPO = Path(__file__).resolve().parents[1]
BOOTSTRAP_KEYS = REPO / "infra/bootstrap-keys.sh"
E2E_SCRIPT = REPO / "scripts/run_operational_truth_e2e.sh"
E2E_AIRFLOW = REPO / "infra/e2e/compose.airflow.yml"
AIRFLOW_SERVICES = ("airflow-init", "airflow", "airflow-scheduler")
COMPOSE_FERNET_REFERENCES = {
    "infra/docker-compose.yml": "${AIRFLOW_FERNET_KEY:?AIRFLOW_FERNET_KEY is required}",
    "infra/terraform/deploy/docker-compose.aws.yml": (
        "${AIRFLOW_FERNET_KEY:?AIRFLOW_FERNET_KEY is required}"
    ),
    "infra/e2e/compose.airflow.yml": "${AIRFLOW_FERNET_KEY:?required}",
}
E2E_FERNET_GENERATOR = "openssl rand -base64 32 | tr '+/' '-_'"


def _is_fernet_key(value: str) -> bool:
    if len(value) != 44:
        return False
    try:
        return len(base64.urlsafe_b64decode(value.encode("ascii"))) == 32
    except (binascii.Error, UnicodeEncodeError):
        return False


def _env_value(path: Path, key: str) -> str:
    values = [
        line.split("=", 1)[1]
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.startswith(f"{key}=")
    ]
    assert len(values) == 1, f"{key} must appear exactly once"
    return values[0]


def _bootstrap_keys(
    tmp_path: Path, env_file: Path, *, entrypoint_config: Path | None = None
) -> subprocess.CompletedProcess:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    fake_openssl = bin_dir / "openssl"
    fake_openssl.write_text(
        "#!/usr/bin/env bash\nprintf 'generated-secret'\n", encoding="utf-8"
    )
    fake_openssl.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["MODECISSIONS_BOOTSTRAP_CONTROL_ROOM_EVIDENCE"] = "false"
    env["MODECISSIONS_AWS_ENTRYPOINT_CONFIG"] = str(
        entrypoint_config or tmp_path / "no-aws-entrypoint.env"
    )
    return subprocess.run(
        ["bash", str(BOOTSTRAP_KEYS), str(env_file)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _run_bootstrap_keys(tmp_path: Path, env_file: Path) -> subprocess.CompletedProcess:
    result = _bootstrap_keys(tmp_path, env_file)
    assert result.returncode == 0, result.stderr
    return result


def test_every_airflow_service_requires_the_shared_fernet_key() -> None:
    for relative, reference in COMPOSE_FERNET_REFERENCES.items():
        services = yaml.safe_load((REPO / relative).read_text(encoding="utf-8"))[
            "services"
        ]
        for service in AIRFLOW_SERVICES:
            environment = services[service]["environment"]
            assert isinstance(environment, dict), f"{relative}:{service}"
            assert environment.get("AIRFLOW__CORE__FERNET_KEY") == reference, (
                f"{relative}:{service} must require the shared Fernet key"
            )


def test_bootstrap_keys_tops_up_a_fernet_shaped_key_once(tmp_path: Path) -> None:
    env_file = tmp_path / "runtime.env"

    first = _run_bootstrap_keys(tmp_path, env_file)
    value = _env_value(env_file, "AIRFLOW_FERNET_KEY")
    assert _is_fernet_key(value)
    assert value not in first.stdout + first.stderr
    assert "[bootstrap-keys] Generated AIRFLOW_FERNET_KEY" in first.stdout

    before = env_file.read_bytes()
    second = _run_bootstrap_keys(tmp_path, env_file)
    assert env_file.read_bytes() == before
    assert "[bootstrap-keys] AIRFLOW_FERNET_KEY already exists, skipping" in (
        second.stdout
    )

    seeded = tmp_path / "seeded.env"
    seeded.write_text("AIRFLOW_FERNET_KEY=preexisting\n", encoding="utf-8")
    _run_bootstrap_keys(tmp_path, seeded)
    assert _env_value(seeded, "AIRFLOW_FERNET_KEY") == "preexisting"


def test_bootstrap_keys_never_mints_the_key_on_aws_hosts(tmp_path: Path) -> None:
    marker = tmp_path / "aws-entrypoint.env"
    marker.write_text("AWS_REGION=us-east-1\n", encoding="utf-8")
    shared = tmp_path / "runtime.env"
    shared.write_text("POSTGRES_PASSWORD=existing\n", encoding="utf-8")
    refused = _bootstrap_keys(tmp_path, shared, entrypoint_config=marker)
    assert refused.returncode != 0
    assert "AIRFLOW_FERNET_KEY is missing" in refused.stderr
    assert "Secrets Manager (modecissions/airflow_fernet_key)" in refused.stderr
    assert shared.read_text(encoding="utf-8") == "POSTGRES_PASSWORD=existing\n"

    deploy_env = tmp_path / "opt" / "infra" / "terraform" / "deploy" / ".env"
    deploy_env.parent.mkdir(parents=True)
    deploy_env.write_text("POSTGRES_PASSWORD=existing\n", encoding="utf-8")
    by_path = _bootstrap_keys(tmp_path, deploy_env)
    assert by_path.returncode != 0
    assert "AIRFLOW_FERNET_KEY is missing" in by_path.stderr
    assert deploy_env.read_text(encoding="utf-8") == "POSTGRES_PASSWORD=existing\n"

    provisioned = tmp_path / "provisioned.env"
    provisioned.write_text('AIRFLOW_FERNET_KEY="from-secrets-manager"\n', encoding="utf-8")
    kept = _bootstrap_keys(tmp_path, provisioned, entrypoint_config=marker)
    assert kept.returncode == 0, kept.stderr
    lines = provisioned.read_text(encoding="utf-8").splitlines()
    assert [line for line in lines if line.startswith("AIRFLOW_FERNET_KEY=")] == [
        'AIRFLOW_FERNET_KEY="from-secrets-manager"'
    ]
    assert any(line.startswith("SECURITY_CONTEXT_SIGNING_KEY=") for line in lines)


def test_e2e_generator_emits_a_valid_fernet_key() -> None:
    assert f'"AIRFLOW_FERNET_KEY=$({E2E_FERNET_GENERATOR})"' in E2E_SCRIPT.read_text(
        encoding="utf-8"
    )
    result = subprocess.run(
        ["bash", "-c", E2E_FERNET_GENERATOR],
        text=True,
        capture_output=True,
        check=True,
    )
    assert _is_fernet_key(result.stdout.strip())


def test_e2e_script_provides_every_required_airflow_variable() -> None:
    compose = E2E_AIRFLOW.read_text(encoding="utf-8")
    script = E2E_SCRIPT.read_text(encoding="utf-8")
    required = set(re.findall(r"\$\{([A-Z][A-Z0-9_]*):\?required\}", compose))
    assert "AIRFLOW_FERNET_KEY" in required
    missing = sorted(
        name for name in required if not re.search(rf"\b{name}\b", script)
    )
    assert not missing, f"not generated by run_operational_truth_e2e.sh: {missing}"


def test_aws_rollout_paths_carry_the_key() -> None:
    entrypoint = (REPO / "scripts/aws-entrypoint.sh").read_text(encoding="utf-8")
    required_block = re.search(
        r"required_secrets=\(([\s\S]*?)\)\n\noptional_secrets=", entrypoint
    )
    assert required_block
    assert re.search(r"^\s*AIRFLOW_FERNET_KEY$", required_block.group(1), re.M)

    secrets = (REPO / "infra/terraform/infra/secretsmanager.tf").read_text(
        encoding="utf-8"
    )
    assert '    "AIRFLOW_FERNET_KEY",\n' in secrets

    start = (REPO / "infra/terraform/deploy/start.sh").read_text(encoding="utf-8")
    assert re.search(r"^check_var AIRFLOW_FERNET_KEY$", start, re.M)

    deploy = (REPO / "scripts/deploy_main_aws.py").read_text(encoding="utf-8")
    assert deploy.index('emit "required env preflight"') < deploy.index("<<'PYSYNC'")

    workflow = yaml.safe_load(
        (REPO / ".github/workflows/docker-image.yml").read_text(encoding="utf-8")
    )
    steps = workflow["jobs"]["compose-validate"]["steps"]
    env = next(step["env"] for step in steps if "env" in step)
    assert env["AIRFLOW_FERNET_KEY"]
    assert not _is_fernet_key(env["AIRFLOW_FERNET_KEY"])

    for relative in ("infra/.env.example", "infra/terraform/deploy/.env.example"):
        example = (REPO / relative).read_text(encoding="utf-8")
        values = re.findall(r"^AIRFLOW_FERNET_KEY=(.*)$", example, re.M)
        assert len(values) == 1, relative
        assert not _is_fernet_key(values[0]), relative
