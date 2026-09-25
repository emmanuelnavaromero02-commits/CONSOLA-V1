from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUTH_RUNNER = ROOT / "infra/terraform/deploy/ghcr-auth-run.sh"

RELEASE_IMAGES = {
    "airflow",
    "banxico",
    "console",
    "hubspot",
    "inegi",
    "mcp-infra",
    "refinement",
    "replicon",
    "salesforce",
    "sap_b1",
    "sap_hcm",
    "sap_s4hana",
    "sap_successfactors",
    "sec_edgar",
    "vault",
    "workspace",
}


def _read(path: str | Path) -> str:
    target = ROOT / path if isinstance(path, str) else path
    return target.read_text(encoding="utf-8")


def _write_executable(path: Path, source: str) -> None:
    path.write_text(source, encoding="utf-8")
    path.chmod(0o700)


def test_private_ghcr_token_is_server_owned_and_never_in_ssm_or_actions() -> None:
    terraform = _read("infra/terraform/infra/secretsmanager.tf")
    iam = _read("infra/terraform/infra/iam.tf")
    runner = _read(AUTH_RUNNER)
    workflow = _read(".github/workflows/deploy-aws.yml")
    entrypoint = _read("scripts/aws-entrypoint.sh")

    assert 'resource "aws_secretsmanager_secret" "ghcr_pull_credentials"' in terraform
    assert 'name        = "modecissions/ghcr_pull_credentials"' in terraform
    assert 'resource "aws_secretsmanager_secret_version"' not in terraform
    assert "secret_string" not in terraform
    assert "aws_secretsmanager_secret.ghcr_pull_credentials.arn" in iam
    assert 'resources = ["*"]' not in iam
    assert "GHCR_PULL_CREDENTIALS" not in entrypoint
    assert "ghcr_pull_credentials" not in entrypoint

    for marker in (
        "secretsmanager get-secret-value",
        "--password-stdin",
        "mktemp -d",
        "DOCKER_CONFIG",
        "docker logout ghcr.io",
        "set +x",
    ):
        assert marker in runner

    assert "GHCR_TOKEN" not in workflow
    assert "github.token" not in workflow
    assert "ghcr_token" not in workflow
    assert "docker login ghcr.io" not in workflow
    assert "packages: read" not in workflow


def test_all_aws_pull_paths_use_the_server_owned_auth_runner() -> None:
    for path in (
        "infra/terraform/deploy/build.sh",
        "infra/terraform/deploy/start.sh",
        "infra/terraform/deploy/update.sh",
        "infra/terraform/deploy/rollback.sh",
        "scripts/deploy_main_aws.py",
        "scripts/aws_rollback.py",
    ):
        assert "ghcr-auth-run.sh" in _read(path), path


def test_wrapped_shell_entrypoints_reexec_an_absolute_script_path() -> None:
    for path in (
        "infra/terraform/deploy/build.sh",
        "infra/terraform/deploy/start.sh",
        "infra/terraform/deploy/update.sh",
        "infra/terraform/deploy/rollback.sh",
    ):
        source = _read(path)
        assert 'SCRIPT_PATH="${SCRIPT_DIR}/$(basename -- "$0")"' in source, path
        assert 'bash "${SCRIPT_PATH}" "$@"' in source, path
        assert 'bash "$0" "$@"' not in source, path


def test_rollback_preflight_pulls_all_16_release_images() -> None:
    source = _read("scripts/aws_rollback.py")

    for image in RELEASE_IMAGES:
        assert image in source
    assert "docker compose" in source
    assert "pull" in source
    assert "16/16" in source
    assert "target console image exists" not in source


def test_deploy_pulls_with_auth_before_migrations_and_never_repulls_after() -> None:
    source = _read("scripts/deploy_main_aws.py")

    authenticated_pull = source.index(
        'bash "$AUTH_RUNNER" docker compose $COMPOSE_FILES pull'
    )
    migrations = source.index('if [ "$RUN_MIGRATIONS" = "1" ]')
    recreate = source.index(
        "docker compose $COMPOSE_FILES up -d --pull never --force-recreate"
    )
    assert authenticated_pull < migrations < recreate


def test_auth_runner_redacts_token_and_removes_temporary_docker_config(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    config_record = tmp_path / "docker-config-path"

    _write_executable(
        fake_bin / "aws",
        """#!/bin/sh
set -eu
printf '%s\\n' '{"username":"release-reader","token":"server-owned-token"}'
""",
    )
    _write_executable(
        fake_bin / "docker",
        """#!/bin/sh
set -eu
case "${1:-}" in
  login)
    supplied="$(cat)"
    test "$supplied" = "server-owned-token"
    mkdir -p "$DOCKER_CONFIG"
    printf '%s\\n' '{"auths":{"ghcr.io":{}}}' > "$DOCKER_CONFIG/config.json"
    printf '%s\\n' "$DOCKER_CONFIG" > "$TEST_DOCKER_CONFIG_RECORD"
    ;;
  logout)
    ;;
  *)
    echo "unexpected docker command" >&2
    exit 9
    ;;
esac
""",
    )

    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "TEST_DOCKER_CONFIG_RECORD": str(config_record),
        "AWS_REGION": "us-east-1",
        "GHCR_OWNER": "emmanuelnavaromero02-commits",
    }
    result = subprocess.run(
        [
            "bash",
            str(AUTH_RUNNER),
            "bash",
            "-c",
            'test "$OMEGA_GHCR_AUTH_ACTIVE" = 1; '
            'test -f "$DOCKER_CONFIG/config.json"; '
            'printf "%s\\n" command-ok',
        ],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "command-ok" in result.stdout
    assert "server-owned-token" not in result.stdout
    assert "server-owned-token" not in result.stderr
    docker_config = Path(config_record.read_text(encoding="utf-8").strip())
    assert not docker_config.exists()


def test_auth_runner_cleans_up_when_wrapped_command_fails(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    config_record = tmp_path / "docker-config-path"

    _write_executable(
        fake_bin / "aws",
        "#!/bin/sh\nset -eu\nprintf '%s\\n' '{\"username\":\"release-reader\",\"token\":\"server-owned-token\"}'\n",
    )
    _write_executable(
        fake_bin / "docker",
        """#!/bin/sh
set -eu
case "${1:-}" in
  login)
    supplied="$(cat)"
    test "$supplied" = "server-owned-token"
    mkdir -p "$DOCKER_CONFIG"
    printf '%s\\n' '{}' > "$DOCKER_CONFIG/config.json"
    printf '%s\\n' "$DOCKER_CONFIG" > "$TEST_DOCKER_CONFIG_RECORD"
    ;;
  logout) ;;
  *) exit 9 ;;
esac
""",
    )

    result = subprocess.run(
        ["bash", str(AUTH_RUNNER), "bash", "-c", "exit 17"],
        cwd=ROOT,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "TEST_DOCKER_CONFIG_RECORD": str(config_record),
        },
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 17
    assert "server-owned-token" not in result.stdout
    assert "server-owned-token" not in result.stderr
    docker_config = Path(config_record.read_text(encoding="utf-8").strip())
    assert not docker_config.exists()


def test_auth_runner_fails_closed_on_malformed_secret(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    command_marker = tmp_path / "command-ran"
    docker_marker = tmp_path / "docker-ran"

    _write_executable(
        fake_bin / "aws",
        "#!/bin/sh\nset -eu\nprintf '%s\\n' 'not-json'\n",
    )
    _write_executable(
        fake_bin / "docker",
        f"#!/bin/sh\nset -eu\ntouch {docker_marker!s}\nexit 0\n",
    )

    result = subprocess.run(
        ["bash", str(AUTH_RUNNER), "touch", str(command_marker)],
        cwd=ROOT,
        env={**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"},
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode != 0
    assert not command_marker.exists()
    assert not docker_marker.exists()
    assert "not-json" not in result.stdout
    assert "not-json" not in result.stderr


def test_auth_runner_fails_closed_when_docker_login_fails(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    command_marker = tmp_path / "command-ran"
    config_record = tmp_path / "docker-config-path"

    _write_executable(
        fake_bin / "aws",
        "#!/bin/sh\nset -eu\nprintf '%s\\n' '{\"username\":\"release-reader\",\"token\":\"server-owned-token\"}'\n",
    )
    _write_executable(
        fake_bin / "docker",
        """#!/bin/sh
set -eu
printf '%s\\n' "$DOCKER_CONFIG" > "$TEST_DOCKER_CONFIG_RECORD"
supplied="$(cat)"
test "$supplied" = "server-owned-token"
exit 42
""",
    )

    result = subprocess.run(
        ["bash", str(AUTH_RUNNER), "touch", str(command_marker)],
        cwd=ROOT,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "TEST_DOCKER_CONFIG_RECORD": str(config_record),
        },
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode != 0
    assert not command_marker.exists()
    assert "server-owned-token" not in result.stdout
    assert "server-owned-token" not in result.stderr
    docker_config = Path(config_record.read_text(encoding="utf-8").strip())
    assert not docker_config.exists()
