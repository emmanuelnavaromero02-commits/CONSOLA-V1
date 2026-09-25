#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from dataclasses import dataclass
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
GOLD_REPAIR_COMPOSE_SHA256 = (
    "a484352a24ad207185d2c3abc546460ef31c6f0a70de8edd6126c1b0078b2b4b"
)
MAX_COMPOSE_INPUT_BYTES = 4 * 1024 * 1024


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


@dataclass(frozen=True)
class ComposeInvocation:
    command: str
    args: tuple[str, ...]
    files: tuple[Path, ...]
    env_files: tuple[Path, ...]
    project_directories: tuple[Path, ...]
    profiles: tuple[str, ...]


def _existing_regular_file(value: str, *, label: str) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    if candidate.is_symlink() or not candidate.is_file():
        raise DockerLockError(f"Docker Compose {label} is not a regular file: {value}")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise DockerLockError(f"Docker Compose path cannot be resolved: {value}") from exc
    if resolved.stat().st_size > MAX_COMPOSE_INPUT_BYTES:
        raise DockerLockError(f"Docker Compose {label} is oversized: {value}")
    return resolved


def _existing_directory(value: str) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    if candidate.is_symlink() or not candidate.is_dir():
        raise DockerLockError(
            "Docker Compose project directory is not a regular directory"
        )
    try:
        return candidate.resolve(strict=True)
    except OSError as exc:
        raise DockerLockError(
            "Docker Compose project directory cannot be resolved"
        ) from exc


def _compose_command(
    args: list[str], *, workspace: Path
) -> ComposeInvocation:
    del workspace
    index = 0
    compose_files: list[Path] = []
    env_files: list[Path] = []
    project_directories: list[Path] = []
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
                compose_files.append(_existing_regular_file(value, label="file"))
            elif key == "--env-file":
                env_files.append(_existing_regular_file(value, label="env file"))
            elif key == "--project-directory":
                project_directories.append(_existing_directory(value))
            elif key == "--profile":
                if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", value) is None:
                    raise DockerLockError("Docker Compose profile is invalid")
                profiles.append(value)
        else:
            raise DockerLockError(f"unreviewed Docker Compose global option: {option}")
    if index >= len(args) or args[index] not in COMPOSE_COMMANDS:
        raise DockerLockError("unknown Docker Compose command after release lock")
    if len(compose_files) != len(set(compose_files)):
        raise DockerLockError("duplicate Docker Compose file")
    if len(env_files) > 1 or len(project_directories) > 1 or len(profiles) > 1:
        raise DockerLockError("duplicate Docker Compose authority option")
    return ComposeInvocation(
        command=args[index],
        args=tuple(args[index + 1 :]),
        files=tuple(compose_files),
        env_files=tuple(env_files),
        project_directories=tuple(project_directories),
        profiles=tuple(profiles),
    )


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


def _paths_overlap(left: str, right: str) -> bool:
    return (
        left == right
        or left.startswith(f"{right}/")
        or right.startswith(f"{left}/")
    )


def _validate_run_mounts(args: list[str], *, workspace: Path) -> None:
    bind_mounts: list[tuple[str, str, bool]] = []
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
            fields = mount.split(":")
            if len(fields) != 3 or fields[2] != "ro":
                raise DockerLockError(
                    "docker run bind volumes require exact read-only syntax"
                )
            source, destination, _mode = fields
            if not Path(source).is_absolute():
                raise DockerLockError("docker run named volumes are forbidden")
            bind_mounts.append((source, destination, True))
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
            if set(fields) != {"type", "src", "dst", "readonly"}:
                raise DockerLockError(
                    "docker run --mount requires exact canonical bind fields"
                )
            if fields["type"] != "bind" or fields["readonly"] != "true":
                raise DockerLockError(
                    "docker run --mount requires an exact read-only bind"
                )
            bind_mounts.append((fields["src"], fields["dst"], True))
        index += 1
    workspace = workspace.resolve(strict=True)
    for source, destination, read_only in bind_mounts:
        raw = os.path.normpath(source)
        resolved = os.path.normpath(str(Path(source).resolve(strict=False)))
        candidates = (raw, resolved)
        protected = ("/run", "/var/run", "/proc", "/sys", "/dev")
        if any(
            _paths_overlap(candidate, protected_path)
            for candidate in candidates
            for protected_path in protected
        ) or any(
            re.fullmatch(r"/proc/(?:self|[0-9]+)/root(?:/.*)?", candidate)
            for candidate in candidates
        ):
            raise DockerLockError("docker run daemon/runtime bind mount is forbidden")
        source_path = Path(source)
        if (
            not read_only
            or source_path.is_symlink()
            or not source_path.is_dir()
        ):
            raise DockerLockError(
                "docker run bind mount must use a regular read-only source directory"
            )
        source_path = source_path.resolve(strict=True)
        init_destination = destination == "/docker-entrypoint-initdb.d"
        canonical_init = (
            source_path.parent.name == "infra"
            and source_path.name in {"init", "init_gold"}
        )
        current_gold = (
            destination == "/current-gold"
            and source_path == workspace / "infra/init_gold"
        )
        if not (init_destination and canonical_init) and not current_gold:
            raise DockerLockError(
                "docker run bind mount source is not release-authorized"
            )


def _release_compose_names(
    invocation: ComposeInvocation, workspace: Path
) -> list[str] | None:
    workspace = workspace.resolve(strict=True)
    expected = {
        (workspace / relative).resolve(strict=False): relative
        for relative in RELEASE_COMPOSE_FILES
    }
    if any(path not in expected for path in invocation.files):
        return None
    return [expected[path] for path in invocation.files]


def _validate_release_compose_globals(
    invocation: ComposeInvocation, *, workspace: Path, compose_files: list[str]
) -> None:
    expected_prefix = list(RELEASE_COMPOSE_FILES[: len(compose_files)])
    if compose_files and compose_files != expected_prefix:
        raise DockerLockError("Docker Compose release files are out of canonical order")
    expected_env = {
        (workspace / relative).resolve(strict=False) for relative in RELEASE_ENV_FILES
    }
    if any(path not in expected_env for path in invocation.env_files):
        raise DockerLockError("Docker Compose env file is not release-authorized")
    workspace_resolved = workspace.resolve(strict=True)
    if any(
        path != workspace_resolved for path in invocation.project_directories
    ):
        raise DockerLockError(
            "Docker Compose project directory is not release-authorized"
        )
    if any(profile != "sap" for profile in invocation.profiles):
        raise DockerLockError("Docker Compose profile is not release-authorized")


def _validate_config_args(args: tuple[str, ...]) -> None:
    flag_options = {"--quiet", "-q"}
    seen: set[str] = set()
    index = 0
    while index < len(args):
        value = args[index]
        key = value.split("=", 1)[0]
        if value in flag_options:
            if value in seen:
                raise DockerLockError(
                    f"duplicate Docker Compose config option: {value}"
                )
            seen.add(value)
            index += 1
            continue
        if key == "--format":
            if key in seen:
                raise DockerLockError("duplicate Docker Compose config format")
            seen.add(key)
            if "=" in value:
                format_name = value.split("=", 1)[1]
                index += 1
            else:
                if index + 1 >= len(args):
                    raise DockerLockError(
                        "Docker Compose config --format has no value"
                    )
                format_name = args[index + 1]
                index += 2
            if format_name not in {"json", "yaml"}:
                raise DockerLockError(
                    "Docker Compose config format is not authorized"
                )
            continue
        raise DockerLockError(
            f"unreviewed Docker Compose config option or argument: {value}"
        )


def _gold_repair_root(
    invocation: ComposeInvocation, *, workspace: Path
) -> Path | None:
    if len(invocation.files) != 1:
        return None
    compose = invocation.files[0]
    try:
        relative = compose.relative_to(workspace.resolve(strict=True).parent)
    except ValueError:
        relative = None
    parts = relative.parts if relative is not None else ()
    path_matches = (
        len(parts) == 4
        and re.fullmatch(r"omega-gold-repair-[a-z0-9_]{6,32}", parts[0])
        is not None
        and re.fullmatch(r"gold-[0-9a-f]{8}", parts[1]) is not None
        and parts[2:] == ("infra", "docker-compose.yml")
    )
    digest = hashlib.sha256(compose.read_bytes()).hexdigest()
    if not path_matches:
        if digest == GOLD_REPAIR_COMPOSE_SHA256:
            raise DockerLockError(
                "Docker Compose Gold repair fixture path is not authorized"
            )
        return None
    if digest != GOLD_REPAIR_COMPOSE_SHA256:
        raise DockerLockError(
            "Docker Compose Gold repair fixture bytes are not authorized"
        )
    if invocation.env_files or invocation.project_directories or invocation.profiles:
        raise DockerLockError(
            "Docker Compose Gold repair fixture has unreviewed global options"
        )
    root = compose.parents[1]
    for relative_dir in ("infra/init", "infra/init_gold"):
        directory = root / relative_dir
        if directory.is_symlink() or not directory.is_dir():
            raise DockerLockError(
                "Docker Compose Gold repair fixture source path is invalid"
            )
    return root


def _validate_gold_psql(args: tuple[str, ...]) -> None:
    if not args or args[0] != "-T":
        raise DockerLockError("Gold repair exec requires exact non-TTY authority")
    index = 1
    if index < len(args) and args[index] == "-e":
        if index + 1 >= len(args):
            raise DockerLockError("Gold repair exec environment has no value")
        environment = args[index + 1]
        if (
            not environment.startswith("PGOPTIONS=")
            or len(environment) > 16_384
            or "\x00" in environment
            or "\n" in environment
            or "\r" in environment
        ):
            raise DockerLockError(
                "Gold repair exec environment is not authorized"
            )
        index += 2
    if index + 2 > len(args):
        raise DockerLockError("Gold repair exec target is incomplete")
    service = args[index]
    index += 1
    if service not in {"postgres", "postgres_gold"}:
        raise DockerLockError("Gold repair exec service is not authorized")
    if index >= len(args) or args[index] != "psql":
        raise DockerLockError("Gold repair exec permits only psql")
    index += 1

    expected_database = (
        "modecissions_gold" if service == "postgres_gold" else "modecissions"
    )
    expected_port = "5433" if service == "postgres_gold" else "5432"
    seen_user = False
    seen_database = False
    seen_port = False
    seen_on_error = False
    seen_query = False
    while index < len(args):
        option = args[index]
        if option == "-v":
            if index + 1 >= len(args):
                raise DockerLockError("Gold repair psql -v has no value")
            variable = args[index + 1]
            if variable == "ON_ERROR_STOP=1":
                if seen_on_error:
                    raise DockerLockError("Gold repair psql option is duplicated")
                seen_on_error = True
            elif variable.startswith("filename="):
                filename = variable.removeprefix("filename=")
                if re.fullmatch(r"[A-Za-z0-9_./-]{1,255}", filename) is None:
                    raise DockerLockError("Gold repair psql filename is invalid")
            elif variable.startswith("checksum="):
                checksum = variable.removeprefix("checksum=")
                if re.fullmatch(r"[0-9a-f]{64}", checksum) is None:
                    raise DockerLockError("Gold repair psql checksum is invalid")
            else:
                raise DockerLockError("Gold repair psql variable is not authorized")
            index += 2
            continue
        if option in {"-U", "-d", "-p"}:
            if index + 1 >= len(args):
                raise DockerLockError(f"Gold repair psql {option} has no value")
            value = args[index + 1]
            if option == "-U":
                if seen_user or value != "postgres":
                    raise DockerLockError("Gold repair psql user is not authorized")
                seen_user = True
            elif option == "-d":
                if seen_database or value != expected_database:
                    raise DockerLockError(
                        "Gold repair psql database is not authorized"
                    )
                seen_database = True
            else:
                if seen_port or value != expected_port:
                    raise DockerLockError("Gold repair psql port is not authorized")
                seen_port = True
            index += 2
            continue
        if option == "-At":
            index += 1
            continue
        if option in {"-c", "-Atc"}:
            if seen_query or index + 1 >= len(args):
                raise DockerLockError("Gold repair psql query is invalid")
            query = args[index + 1]
            if not query or len(query) > 262_144 or "\x00" in query:
                raise DockerLockError("Gold repair psql query is invalid")
            seen_query = True
            index += 2
            continue
        raise DockerLockError(f"Gold repair psql option is not authorized: {option}")
    if not seen_on_error or not seen_user or not seen_database:
        raise DockerLockError("Gold repair psql identity is incomplete")
    if service == "postgres_gold" and not seen_port:
        raise DockerLockError("Gold repair psql Gold port is missing")


def _validate_gold_command(invocation: ComposeInvocation) -> None:
    if invocation.command == "up":
        if list(invocation.args) != [
            "-d",
            "--no-build",
            "--pull",
            "never",
            "postgres",
            "postgres_gold",
        ]:
            raise DockerLockError("Gold repair Compose up argv is not authorized")
        return
    if invocation.command == "exec":
        _validate_gold_psql(invocation.args)
        return
    if invocation.command == "down":
        if invocation.args != ("-v",):
            raise DockerLockError("Gold repair Compose down argv is not authorized")
        return
    raise DockerLockError(
        f"Gold repair Compose command is not authorized: {invocation.command}"
    )


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
        _validate_run_mounts(remainder, workspace=workspace)
        _validate_docker_run(remainder)
        return
    if command == "compose":
        invocation = _compose_command(remainder, workspace=workspace)
        if invocation.command == "version":
            if (
                invocation.files
                or invocation.env_files
                or invocation.project_directories
                or invocation.profiles
                or invocation.args not in {(), ("--short",)}
            ):
                raise DockerLockError("Docker Compose version argv is not authorized")
            return
        if not invocation.files:
            raise DockerLockError("Docker Compose explicit file is required")
        if invocation.command == "config":
            _validate_config_args(invocation.args)
            return

        gold_root = _gold_repair_root(invocation, workspace=workspace)
        if gold_root is not None:
            _validate_gold_command(invocation)
            return

        compose_files = _release_compose_names(invocation, workspace)
        if compose_files is None:
            raise DockerLockError(
                "Docker Compose path is not release-authorized for daemon access"
            )
        _validate_release_compose_globals(
            invocation, workspace=workspace, compose_files=compose_files
        )
        compose_args = list(invocation.args)
        if invocation.command == "up":
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
        if invocation.command == "down" and any(
            value == "--rmi" or value.startswith("--rmi=")
            for value in compose_args
        ):
            raise DockerLockError("docker compose down --rmi is forbidden")
        if invocation.command not in SAFE_COMPOSE_COMMANDS:
            raise DockerLockError(
                f"docker compose {invocation.command} is forbidden after release lock"
            )
        return
    if command not in SAFE_DOCKER_COMMANDS:
        raise DockerLockError(f"unknown docker command after release lock: {command}")


def _validate_control_environment(
    environment: dict[str, str], *, args: list[str], workspace: Path
) -> None:
    poisoned = sorted(
        key
        for key, value in environment.items()
        if value
        and key.startswith(("COMPOSE_", "DOCKER_"))
        and key != "COMPOSE_PROJECT_NAME"
    )
    if poisoned:
        raise DockerLockError(
            f"Docker control environment is forbidden after release lock: {poisoned[0]}"
        )
    project_name = environment.get("COMPOSE_PROJECT_NAME", "")
    if not project_name:
        if args[:1] == ["compose"]:
            invocation = _compose_command(args[1:], workspace=workspace)
            if (
                invocation.command != "config"
                and _gold_repair_root(invocation, workspace=workspace) is not None
            ):
                raise DockerLockError(
                    "Docker Compose Gold repair project name is missing"
                )
        return
    if re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,62}", project_name) is None:
        raise DockerLockError("Docker Compose project name is invalid")
    if args[:1] != ["compose"]:
        raise DockerLockError(
            "Docker Compose project name has no authorized command scope"
        )
    invocation = _compose_command(args[1:], workspace=workspace)
    if invocation.command == "config":
        return
    gold_root = _gold_repair_root(invocation, workspace=workspace)
    if gold_root is None or project_name != gold_root.name:
        raise DockerLockError(
            "Docker Compose project name is not bound to the Gold repair fixture"
        )


def _trusted_docker_config(value: Path) -> Path:
    if not value.is_absolute() or value.is_symlink() or not value.is_dir():
        raise DockerLockError("trusted Docker config directory is invalid")
    try:
        resolved = value.resolve(strict=True)
        mode = resolved.stat().st_mode
        entries = tuple(resolved.iterdir())
    except OSError as exc:
        raise DockerLockError("trusted Docker config directory cannot be read") from exc
    if mode & 0o222 or entries:
        raise DockerLockError(
            "trusted Docker config directory is writable or nonempty"
        )
    return resolved


def _execution_environment(environment: dict[str, str]) -> dict[str, str]:
    clean = {
        key: value
        for key, value in environment.items()
        if not key.startswith(("COMPOSE_", "DOCKER_"))
        and key not in {"HOME", "XDG_CONFIG_HOME"}
    }
    project_name = environment.get("COMPOSE_PROJECT_NAME", "")
    if project_name:
        clean["COMPOSE_PROJECT_NAME"] = project_name
    return clean


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, default=REPO)
    parser.add_argument("--real", required=True)
    parser.add_argument("--trusted-config", type=Path, required=True)
    parser.add_argument("docker_args", nargs=argparse.REMAINDER)
    parsed = parser.parse_args(argv)
    args = parsed.docker_args
    if args[:1] == ["--"]:
        args = args[1:]
    try:
        workspace = parsed.workspace.resolve(strict=True)
        validate(args, workspace=workspace)
        _validate_control_environment(
            dict(os.environ), args=args, workspace=workspace
        )
        trusted_config = _trusted_docker_config(parsed.trusted_config)
    except DockerLockError as exc:
        print(f"RELEASE DOCKER LOCK BLOCKED: {exc}", file=sys.stderr)
        return 97
    os.execve(
        parsed.real,
        [parsed.real, "--config", str(trusted_config), *args],
        _execution_environment(dict(os.environ)),
    )
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
