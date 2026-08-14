from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.release_docker_lock import DockerLockError, validate

REPO = Path(__file__).resolve().parents[1]
RELEASE_CHAIN = [
    "-f",
    "infra/docker-compose.yml",
    "-f",
    "infra/docker-compose.dev.yml",
    "-f",
    "infra/terraform-gcp/release/docker-compose.release.yml",
]


@pytest.mark.parametrize(
    "args",
    [
        ["--context", "default", "pull", "postgres:latest"],
        ["pull", "postgres:latest"],
        ["build", "."],
        ["buildx", "build", "."],
        ["tag", "a", "b"],
        ["load"],
        ["import", "archive"],
        ["commit", "container"],
        ["image", "pull", "postgres:latest"],
        ["compose", "pull"],
        ["compose", "build"],
        ["compose", "create"],
        ["compose", "run", "console"],
        ["compose", "up", "-d", "--pull", "always", "--no-build"],
        ["compose", "up", "-d", "--pull", "never"],
        ["compose", "up", "-d", "--no-build", "--pull", "never", "--build"],
        ["compose", "up", "--no-build", "--pull", "never", "svc", "--build"],
        ["compose", "up", "--no-build", "--pull", "never", "svc", "--pull", "always"],
        ["compose", "up", "--no-build", "--pull", "never", "--unreviewed", "svc"],
        ["compose", "-f", "ps", "build", "."],
        ["compose", "-f", "/tmp/evil-compose.yml", "up", "--no-build", "--pull", "never"],
        ["compose", "--env-file", "/tmp/evil.env", "-f", "infra/docker-compose.yml", "ps"],
        ["compose", "--project-directory", "/tmp", "-f", "infra/docker-compose.yml", "ps"],
        ["compose", "--project-name", "logs", "pull"],
        ["run", "alpine", "sh", "--pull=never"],
        ["run", "--name", "--pull=never", "alpine"],
        ["run", "--pull=never", "--pull=always", "alpine"],
        ["run", "--pull=never", "--privileged", "alpine"],
        ["run", "--pull=never", "--privileged=true", "alpine"],
        ["run", "--pull=never", "--use-api-socket", "alpine"],
        ["run", "--pull=never", "--cap-add", "SYS_ADMIN", "alpine"],
        ["run", "--pull=never", "--device=/dev/sda", "alpine"],
        ["run", "--pull=never", "--security-opt", "seccomp=unconfined", "alpine"],
        ["run", "--pull=never", "--detach-keys", "--pull=never", "alpine"],
        ["run", "--pull=never", "-v", "/var/run/docker.sock:/var/run/docker.sock", "alpine"],
        ["run", "--pull=never", "-v", "/run/docker.sock:/sock", "alpine"],
        ["run", "--pull=never", "--mount", "type=bind,src=/run/docker.sock,dst=/sock", "alpine"],
        ["run", "--pull=never", "--mount", "type=bind,src=/,dst=/host", "alpine"],
        ["run", "--pull=never", "--mount", "type=bind,src=/proc/1/root/run/docker.sock,dst=/sock", "alpine"],
        ["run", "--pull=never", "--mount", "type=volume,src=sock,dst=/sock,volume-driver=local,volume-opt=type=none,volume-opt=o=bind,volume-opt=device=/run/docker.sock", "alpine"],
        ["run", "--pull=never", "--volume", "named-volume:/data", "alpine"],
        ["run", "--pull=never", "--volume-driver", "local", "alpine"],
        ["run", "--pull=never", "--volumes-from", "docker-proxy", "alpine"],
        ["run", "--pull=never", "-v", "/:/host", "alpine"],
        ["compose", "down", "--rmi", "all"],
        ["compose", "down", "--rmi=local"],
        ["unknown-mutator"],
    ],
)
def test_mutating_and_parser_bypass_commands_are_blocked(args: list[str]) -> None:
    with pytest.raises(DockerLockError):
        validate(args)


@pytest.mark.parametrize(
    "args",
    [
        ["image", "inspect", "postgres:15.18"],
        ["image", "ls", "--digests"],
        ["run", "--pull=never", "--rm", "postgres:15.18"],
        ["run", "--rm", "--pull", "never", "--name", "probe", "postgres:15.18"],
        [
            "compose",
            "-f",
            "infra/docker-compose.yml",
            "-f",
            "infra/docker-compose.dev.yml",
            "-f",
            "infra/terraform-gcp/release/docker-compose.release.yml",
            "--profile",
            "sap",
            "up",
            "-d",
            "--no-build",
            "--pull",
            "never",
            "console",
        ],
        ["compose", "-f", "infra/docker-compose.yml", "ps", "--all"],
        ["compose", "version"],
        ["exec", "container", "true"],
        ["inspect", "container"],
        ["rm", "-f", "container"],
    ],
)
def test_reviewed_non_image_mutating_commands_pass(args: list[str]) -> None:
    validate(args)


@pytest.mark.parametrize(
    "args",
    (
        [
            "run",
            "--pull=never",
            "--mount",
            "type=volume,src=sock,dst=/sock,volume-driver=local,"
            "volume-opt=type=none,volume-opt=o=bind,"
            "volume-opt=device=/run/docker.sock",
            "alpine",
        ],
        [
            "compose",
            "-f",
            "/tmp/evil-compose.yml",
            "up",
            "--no-build",
            "--pull",
            "never",
        ],
    ),
)
def test_cli_bypass_is_rc97_and_never_executes_real_docker(
    tmp_path: Path, args: list[str]
) -> None:
    sentinel = tmp_path / "real-docker-executed"
    real = tmp_path / "docker-real"
    real.write_text(
        "#!/bin/sh\nprintf executed >\"${DOCKER_EXEC_SENTINEL}\"\n",
        encoding="utf-8",
    )
    real.chmod(0o755)

    result = subprocess.run(
        [
            sys.executable,
            str(REPO / "scripts/release_docker_lock.py"),
            "--workspace",
            str(REPO),
            "--real",
            str(real),
            "--",
            *args,
        ],
        cwd=REPO,
        env={**os.environ, "DOCKER_EXEC_SENTINEL": str(sentinel)},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 97, result.stdout + result.stderr
    assert not sentinel.exists()


@pytest.mark.parametrize("key", ("DOCKER_HOST", "DOCKER_CONTEXT", "COMPOSE_FILE"))
def test_cli_control_environment_is_rc97_and_never_executes_real_docker(
    tmp_path: Path, key: str
) -> None:
    sentinel = tmp_path / "real-docker-executed"
    real = tmp_path / "docker-real"
    real.write_text(
        "#!/bin/sh\nprintf executed >\"${DOCKER_EXEC_SENTINEL}\"\n",
        encoding="utf-8",
    )
    real.chmod(0o755)

    result = subprocess.run(
        [
            sys.executable,
            str(REPO / "scripts/release_docker_lock.py"),
            "--workspace",
            str(REPO),
            "--real",
            str(real),
            "--",
            "inspect",
            "mode_console",
        ],
        cwd=REPO,
        env={
            **os.environ,
            "DOCKER_EXEC_SENTINEL": str(sentinel),
            key: "/tmp/attacker-control",
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 97, result.stdout + result.stderr
    assert not sentinel.exists()
