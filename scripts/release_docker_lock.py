#!/usr/bin/env python3
"""Fail-closed Docker argv guard used after the release image pre-pull lock."""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path


class DockerLockError(RuntimeError):
    pass


REPO = Path(__file__).resolve().parents[1]
RELEASE_COMPOSE_FILES = (
    "infra/docker-compose.yml",
    "infra/docker-compose.dev.yml",
    "infra/terraform-gcp/release/docker-compose.release.yml",
)
RELEASE_ENV_FILES = ("infra/.env",)


DENIED_DOCKER_COMMANDS = {
    "build",
    "buildx",
    "commit",
    "import",
    "load",
    "pull",
    "push",
    "tag",
}
SAFE_DOCKER_COMMANDS = {
    "cp",
    "exec",
    "info",
    "inspect",
    "kill",
    "logs",
    "port",
    "ps",
    "restart",
    "rm",
    "start",
    "stop",
    "version",
    "wait",
}
COMPOSE_COMMANDS = {
    "build",
    "config",
    "create",
    "down",
    "exec",
    "images",
    "kill",
    "logs",
    "pause",
    "port",
    "ps",
    "pull",
    "restart",
    "rm",
    "run",
    "start",
    "stop",
    "top",
    "unpause",
    "up",
    "version",
    "wait",
}
SAFE_COMPOSE_COMMANDS = COMPOSE_COMMANDS - {"build", "create", "pull", "run"}
COMPOSE_GLOBAL_VALUE_OPTIONS = {
    "--env-file",
    "--file",
    "--profile",
    "--project-directory",
    "-f",
}
COMPOSE_UP_VALUE_OPTIONS = {
    "--attach", "--exit-code-from", "--no-attach", "--pull", "--scale",
    "--timeout", "--wait-timeout",
}
COMPOSE_UP_FLAG_OPTIONS = {
    "--abort-on-container-exit", "--abort-on-container-failure",
    "--always-recreate-deps", "--attach-dependencies", "--build", "--detach",
    "--force-recreate", "--menu", "--no-build", "--no-color", "--no-deps",
    "--no-log-prefix", "--no-recreate", "--no-start", "--quiet-pull",
    "--remove-orphans", "--renew-anon-volumes", "--timestamps", "--wait",
    "-V", "-d",
}
DOCKER_RUN_VALUE_OPTIONS = {
    "--add-host", "--annotation", "--attach", "--blkio-weight",
    "--blkio-weight-device", "--cap-add", "--cap-drop", "--cgroup-parent",
    "--cgroupns", "--cidfile", "--cpu-period", "--cpu-quota", "--cpu-rt-period",
    "--cpu-rt-runtime", "--cpu-shares", "--cpus", "--cpuset-cpus",
    "--cpuset-mems", "--device", "--device-cgroup-rule", "--device-read-bps",
    "--detach-keys", "--device-read-iops", "--device-write-bps", "--device-write-iops", "--dns",
    "--dns-option", "--dns-search", "--domainname", "--entrypoint", "--env",
    "--env-file", "--expose", "--gpus", "--group-add", "--health-cmd",
    "--health-interval", "--health-retries", "--health-start-interval",
    "--health-start-period", "--health-timeout", "--hostname", "--init-path",
    "--ip", "--ip6", "--ipc", "--isolation", "--kernel-memory", "--label",
    "--label-file", "--link", "--link-local-ip", "--log-driver", "--log-opt",
    "--mac-address", "--memory", "--memory-reservation", "--memory-swap",
    "--memory-swappiness", "--mount", "--name", "--network", "--network-alias",
    "--oom-score-adj", "--pid", "--pids-limit", "--platform", "--publish",
    "--restart", "--runtime", "--security-opt", "--shm-size",
    "--stop-signal", "--stop-timeout", "--storage-opt", "--sysctl", "--tmpfs",
    "--ulimit", "--user", "--userns", "--uts", "--volume", "--volume-driver",
    "--volumes-from", "--workdir", "-a", "-c", "-e", "-h", "-l", "-m",
    "-p", "-u", "-v", "-w",
}
DOCKER_RUN_FLAG_OPTIONS = {
    "--detach", "--disable-content-trust", "--help", "--init", "--interactive",
    "--no-healthcheck", "--oom-kill-disable", "--publish-all", "--quiet",
    "--read-only", "--rm", "--sig-proxy", "--tty", "-P", "-d", "-i", "-q",
    "-t",
}


def _exact_release_path(value: str, workspace: Path, allowed: tuple[str, ...]) -> str:
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise DockerLockError(f"Docker Compose path cannot be resolved: {value}") from exc
    expected = {
        (workspace / relative).resolve(strict=True): relative for relative in allowed
    }
    if resolved not in expected:
        raise DockerLockError(f"Docker Compose path is not release-authorized: {value}")
    return expected[resolved]


def _compose_command(
    args: list[str], *, workspace: Path
) -> tuple[str, list[str], list[str]]:
    index = 0
    compose_files: list[str] = []
    env_files: list[str] = []
    project_directories: list[str] = []
    profiles: list[str] = []
    while index < len(args) and args[index].startswith("-"):
        option = args[index]
        key = option.split("=", 1)[0]
        if key in COMPOSE_GLOBAL_VALUE_OPTIONS:
            if "=" in option:
                value = option.split("=", 1)[1]
                index += 1
            else:
                if index + 1 >= len(args):
                    raise DockerLockError(
                        f"Docker Compose global option has no value: {option}"
                    )
                value = args[index + 1]
                index += 2
            if not value or value.startswith("-"):
                raise DockerLockError(
                    f"Docker Compose global option has invalid value: {option}"
                )
            if key in {"--file", "-f"}:
                compose_files.append(
                    _exact_release_path(value, workspace, RELEASE_COMPOSE_FILES)
                )
            elif key == "--env-file":
                env_files.append(
                    _exact_release_path(value, workspace, RELEASE_ENV_FILES)
                )
            elif key == "--project-directory":
                candidate = Path(value)
                if not candidate.is_absolute():
                    candidate = Path.cwd() / candidate
                try:
                    resolved = candidate.resolve(strict=True)
                except OSError as exc:
                    raise DockerLockError(
                        "Docker Compose project directory cannot be resolved"
                    ) from exc
                if resolved != workspace.resolve(strict=True):
                    raise DockerLockError(
                        "Docker Compose project directory is not release-authorized"
                    )
                project_directories.append(str(resolved))
            elif key == "--profile":
                if value != "sap":
                    raise DockerLockError("Docker Compose profile is not authorized")
                profiles.append(value)
        else:
            raise DockerLockError(f"unreviewed Docker Compose global option: {option}")
    if index >= len(args) or args[index] not in COMPOSE_COMMANDS:
        raise DockerLockError("unknown Docker Compose command after release lock")
    if len(compose_files) != len(set(compose_files)):
        raise DockerLockError("duplicate Docker Compose release file")
    expected_prefix = list(RELEASE_COMPOSE_FILES[: len(compose_files)])
    if compose_files and compose_files != expected_prefix:
        raise DockerLockError("Docker Compose release files are out of canonical order")
    if len(env_files) > 1 or len(project_directories) > 1 or len(profiles) > 1:
        raise DockerLockError("duplicate Docker Compose release authority option")
    return args[index], args[index + 1 :], compose_files


def _has_pull_never(args: list[str]) -> bool:
    values: list[str] = []
    for index, value in enumerate(args):
        if value.startswith("--pull="):
            values.append(value.split("=", 1)[1])
        elif value == "--pull" and index + 1 < len(args):
            values.append(args[index + 1])
    return values == ["never"]


def _compose_up_option_prefix(args: list[str]) -> tuple[list[str], list[str]]:
    prefix: list[str] = []
    index = 0
    while index < len(args):
        value = args[index]
        if not value.startswith("-"):
            break
        prefix.append(value)
        key = value.split("=", 1)[0]
        if key in COMPOSE_UP_VALUE_OPTIONS and "=" not in value:
            if index + 1 >= len(args):
                raise DockerLockError(f"Docker Compose option has no value: {value}")
            prefix.append(args[index + 1])
            index += 2
        elif key in COMPOSE_UP_VALUE_OPTIONS or value in COMPOSE_UP_FLAG_OPTIONS:
            index += 1
        else:
            raise DockerLockError(f"unreviewed Docker Compose up option: {value}")
    return prefix, args[index:]


def _validate_docker_run(args: list[str]) -> None:
    pulls: list[str] = []
    index = 0
    image_found = False
    while index < len(args):
        value = args[index]
        if not value.startswith("-") or value == "-":
            image_found = True
            break
        key = value.split("=", 1)[0]
        if key == "--pull":
            if "=" in value:
                pulls.append(value.split("=", 1)[1])
                index += 1
            else:
                if index + 1 >= len(args):
                    raise DockerLockError("docker run --pull has no value")
                pulls.append(args[index + 1])
                index += 2
            continue
        if key in DOCKER_RUN_VALUE_OPTIONS:
            if "=" in value or (key.startswith("-") and not key.startswith("--") and value != key):
                index += 1
            else:
                if index + 1 >= len(args):
                    raise DockerLockError(f"docker run option has no value: {value}")
                if args[index + 1].startswith("-"):
                    raise DockerLockError(
                        f"docker run option value is another option: {value}"
                    )
                index += 2
            continue
        if value in DOCKER_RUN_FLAG_OPTIONS:
            index += 1
            continue
        raise DockerLockError(f"unreviewed docker run option: {value}")
    if not image_found or pulls != ["never"]:
        raise DockerLockError(
            "docker run requires exactly one --pull=never before IMAGE"
        )


def _reject_daemon_mounts(args: list[str]) -> None:
    sources: list[str] = []
    index = 0
    while index < len(args):
        value = args[index]
        key = value.split("=", 1)[0]
        if key == "--volumes-from":
            raise DockerLockError("docker run --volumes-from is forbidden")
        if key == "--volume-driver":
            raise DockerLockError("docker run volume drivers are forbidden")
        if key in {"-v", "--volume"}:
            mount = value.split("=", 1)[1] if "=" in value else (
                args[index + 1] if index + 1 < len(args) else ""
            )
            source = mount.split(":", 1)[0]
            if not Path(source).is_absolute():
                raise DockerLockError("docker run named volumes are forbidden")
            sources.append(source)
        elif key == "--mount":
            mount = value.split("=", 1)[1] if "=" in value else (
                args[index + 1] if index + 1 < len(args) else ""
            )
            fields: dict[str, str] = {}
            for part in mount.split(","):
                field, separator, field_value = part.partition("=")
                if not separator or not field or field in fields:
                    raise DockerLockError("docker run mount syntax is not canonical")
                fields[field] = field_value
            mount_type = fields.get("type")
            if (
                mount_type == "volume"
                or "volume-driver" in fields
                or "volume-opt" in fields
            ):
                raise DockerLockError("docker run volume mounts are forbidden")
            if mount_type not in {"bind", "tmpfs"}:
                raise DockerLockError("docker run mount type is not authorized")
            source = fields.get("src", fields.get("source"))
            if mount_type == "bind" and not source:
                raise DockerLockError("docker run bind mount has no source")
            if source:
                sources.append(source)
        index += 1
    for source in sources:
        raw = os.path.normpath(source)
        resolved = os.path.normpath(str(Path(source).resolve(strict=False)))
        candidates = (raw, resolved)
        if any(
            candidate == "/"
            or candidate in {"/run", "/var/run", "/proc", "/sys", "/dev"}
            or candidate.startswith(("/run/", "/var/run/", "/proc/", "/sys/", "/dev/"))
            for candidate in candidates
        ) or any(
            re.fullmatch(r"/proc/(?:self|[0-9]+)/root(?:/.*)?", candidate)
            for candidate in candidates
        ):
            raise DockerLockError("docker run daemon/runtime bind mount is forbidden")


def validate(args: list[str], *, workspace: Path = REPO) -> None:
    if not args or args[0].startswith("-"):
        raise DockerLockError("Docker global options are forbidden after release lock")
    command = args[0]
    remainder = args[1:]
    if command in DENIED_DOCKER_COMMANDS:
        raise DockerLockError(f"docker {command} is forbidden after release lock")
    if command == "image":
        if not remainder or remainder[0] not in {"inspect", "ls"}:
            raise DockerLockError(
                "only docker image inspect/ls is allowed after release lock"
            )
        return
    if command == "run":
        if any(
            value.split("=", 1)[0] in {"--privileged", "--use-api-socket"}
            for value in remainder
        ) or any(
            value.split("=", 1)[0].startswith(("--cap-", "--device"))
            or value.split("=", 1)[0] == "--security-opt"
            for value in remainder
        ) or any(
            "/var/run/docker.sock" in value for value in remainder
        ):
            raise DockerLockError("docker run daemon/privileged access is forbidden")
        _reject_daemon_mounts(remainder)
        _validate_docker_run(remainder)
        return
    if command == "compose":
        compose_command, compose_args, compose_files = _compose_command(
            remainder, workspace=workspace
        )
        if compose_command != "version" and not compose_files:
            raise DockerLockError("Docker Compose release file is required")
        if compose_command == "up":
            if compose_files != list(RELEASE_COMPOSE_FILES):
                raise DockerLockError(
                    "docker compose up requires the exact release Compose chain"
                )
            option_prefix, services = _compose_up_option_prefix(compose_args)
            if (
                "--build" in option_prefix
                or "--no-build" not in option_prefix
                or not _has_pull_never(option_prefix)
                or any(value.startswith("-") for value in services)
            ):
                raise DockerLockError(
                    "docker compose up requires --no-build and --pull never"
                )
            return
        if compose_command == "down" and any(
            value == "--rmi" or value.startswith("--rmi=")
            for value in compose_args
        ):
            raise DockerLockError("docker compose down --rmi is forbidden")
        if compose_command not in SAFE_COMPOSE_COMMANDS:
            raise DockerLockError(
                f"docker compose {compose_command} is forbidden after release lock"
            )
        return
    if command not in SAFE_DOCKER_COMMANDS:
        raise DockerLockError(f"unknown docker command after release lock: {command}")


def _validate_control_environment(environment: dict[str, str]) -> None:
    poisoned = sorted(
        key
        for key, value in environment.items()
        if value and key.startswith(("COMPOSE_", "DOCKER_"))
    )
    if poisoned:
        raise DockerLockError(
            f"Docker control environment is forbidden after release lock: {poisoned[0]}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, default=REPO)
    parser.add_argument("--real", required=True)
    parser.add_argument("docker_args", nargs=argparse.REMAINDER)
    parsed = parser.parse_args(argv)
    args = parsed.docker_args
    if args[:1] == ["--"]:
        args = args[1:]
    try:
        _validate_control_environment(dict(os.environ))
        validate(args, workspace=parsed.workspace.resolve(strict=True))
    except DockerLockError as exc:
        print(f"RELEASE DOCKER LOCK BLOCKED: {exc}", file=sys.stderr)
        return 97
    os.execv(parsed.real, [parsed.real, *args])
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
