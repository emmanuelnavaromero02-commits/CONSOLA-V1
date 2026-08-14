from __future__ import annotations

import pytest

from scripts.release_docker_lock import DockerLockError, validate


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
            "--env-file",
            "infra/.env",
            "-f",
            "infra/docker-compose.yml",
            "--profile",
            "sap",
            "up",
            "-d",
            "--no-build",
            "--pull",
            "never",
            "console",
        ],
        ["compose", "ps", "--all"],
        ["exec", "container", "true"],
        ["inspect", "container"],
        ["rm", "-f", "container"],
    ],
)
def test_reviewed_non_image_mutating_commands_pass(args: list[str]) -> None:
    validate(args)
