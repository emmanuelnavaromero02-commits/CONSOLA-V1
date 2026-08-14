#!/usr/bin/env python3
"""Fail-closed Docker argv guard used after the release image pre-pull lock."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


class DockerLockError(RuntimeError):
    pass


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
    "--ansi", "--env-file", "--file", "--parallel", "--profile",
    "--progress", "--project-directory", "--project-name", "-f", "-p",
}
COMPOSE_GLOBAL_FLAG_OPTIONS = {"--all-resources", "--compatibility", "--dry-run"}
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


def _compose_command(args: list[str]) -> tuple[str, list[str]]:
    index = 0
    while index < len(args) and args[index].startswith("-"):
        option = args[index]
        key = option.split("=", 1)[0]
        if key in COMPOSE_GLOBAL_VALUE_OPTIONS:
            index += 1 if "=" in option else 2
        elif option in COMPOSE_GLOBAL_FLAG_OPTIONS:
            index += 1
        else:
            raise DockerLockError(f"unreviewed Docker Compose global option: {option}")
    if index >= len(args) or args[index] not in COMPOSE_COMMANDS:
        raise DockerLockError("unknown Docker Compose command after release lock")
    return args[index], args[index + 1 :]


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
        if key in {"-v", "--volume"}:
            mount = value.split("=", 1)[1] if "=" in value else (
                args[index + 1] if index + 1 < len(args) else ""
            )
            sources.append(mount.split(":", 1)[0])
        elif key == "--mount":
            mount = value.split("=", 1)[1] if "=" in value else (
                args[index + 1] if index + 1 < len(args) else ""
            )
            fields = dict(
                part.split("=", 1) for part in mount.split(",") if "=" in part
            )
            source = fields.get("src", fields.get("source"))
            if source:
                sources.append(source)
        index += 1
    for source in sources:
        raw = source.rstrip("/")
        resolved = str(Path(source).resolve(strict=False)).rstrip("/")
        if any(
            candidate == prefix
            or candidate.startswith(prefix + "/")
            or prefix.startswith(candidate + "/")
            for candidate in (raw, resolved)
            for prefix in ("/run", "/var/run")
        ):
            raise DockerLockError("docker run daemon/runtime bind mount is forbidden")


def validate(args: list[str]) -> None:
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
        compose_command, compose_args = _compose_command(remainder)
        if compose_command == "up":
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--real", required=True)
    parser.add_argument("docker_args", nargs=argparse.REMAINDER)
    parsed = parser.parse_args(argv)
    args = parsed.docker_args
    if args[:1] == ["--"]:
        args = args[1:]
    try:
        validate(args)
    except DockerLockError as exc:
        print(f"RELEASE DOCKER LOCK BLOCKED: {exc}", file=sys.stderr)
        return 97
    os.execv(parsed.real, [parsed.real, *args])
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
