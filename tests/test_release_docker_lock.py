from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.release_docker_lock import (
    GOLD_REPAIR_COMPOSE_SHA256,
    DockerLockError,
    validate,
)

REPO = Path(__file__).resolve().parents[1]
RELEASE_CHAIN = [
    "-f",
    "infra/docker-compose.yml",
    "-f",
    "infra/docker-compose.dev.yml",
    "-f",
    "infra/terraform-gcp/release/docker-compose.release.yml",
]
GOLD_FIXTURE = REPO / "tests/fixtures/docker-compose.gold-repair.yml"


def _gold_sandbox(tmp_path: Path) -> tuple[Path, Path, Path]:
    workspace = tmp_path / "checkout"
    workspace.mkdir()
    root = tmp_path / "omega-gold-repair-abc123" / "gold-deadbeef"
    (root / "infra/init").mkdir(parents=True)
    (root / "infra/init_gold").mkdir()
    compose = root / "infra/docker-compose.yml"
    shutil.copy2(GOLD_FIXTURE, compose)
    return workspace, root, compose


def _run_guard(
    tmp_path: Path,
    args: list[str],
    *,
    workspace: Path = REPO,
    environment: dict[str, str] | None = None,
    trusted_config: Path | None = None,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    sentinel = tmp_path / "real-docker-executed"
    real = tmp_path / "docker-real"
    if trusted_config is None:
        trusted_config = tmp_path / "trusted-docker-config"
        trusted_config.mkdir()
        trusted_config.chmod(0o555)
    real.write_text(
        "#!/bin/sh\nprintf executed >\"${LOCK_EXEC_SENTINEL}\"\n",
        encoding="utf-8",
    )
    real.chmod(0o755)
    result = subprocess.run(
        [
            sys.executable,
            str(REPO / "scripts/release_docker_lock.py"),
            "--workspace",
            str(workspace),
            "--real",
            str(real),
            "--trusted-config",
            str(trusted_config),
            "--",
            *args,
        ],
        cwd=REPO,
        env={
            **os.environ,
            "LOCK_EXEC_SENTINEL": str(sentinel),
            **(environment or {}),
        },
        text=True,
        capture_output=True,
        check=False,
    )
    return result, sentinel


def _real_docker_binary() -> Path:
    for candidate in (
        Path("/usr/bin/docker"),
        Path("/usr/local/bin/docker"),
        Path("/opt/homebrew/bin/docker"),
    ):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    raise AssertionError("a real Docker CLI is required for the release lock tests")


def _docker_test_environment(**extra: str) -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("COMPOSE_", "DOCKER_"))
    }
    environment.update(extra)
    return environment


def test_gold_repair_fixture_bytes_match_runtime_authority() -> None:
    assert hashlib.sha256(GOLD_FIXTURE.read_bytes()).hexdigest() == (
        GOLD_REPAIR_COMPOSE_SHA256
    )


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
        ["run", "--pull=never", "-v", "/var:/hostvar", "alpine"],
        ["run", "--pull=never", "-v", "/var:/hostvar:ro", "alpine"],
        ["run", "--pull=never", "-v", "/etc:/hostetc", "alpine"],
        ["run", "--pull=never", "--mount", "type=bind,src=/run/docker.sock,dst=/sock", "alpine"],
        ["run", "--pull=never", "--mount", "type=bind,src=/var,dst=/hostvar", "alpine"],
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


def test_reviewed_postgres_init_bind_is_exact_and_read_only() -> None:
    for option, value in (
        (
            "-v",
            f"{REPO / 'infra/init'}:/docker-entrypoint-initdb.d:ro",
        ),
        (
            "--mount",
            f"type=bind,src={REPO / 'infra/init'},"
            "dst=/docker-entrypoint-initdb.d,readonly=true",
        ),
    ):
        validate(
            [
                "run",
                "--pull=never",
                option,
                value,
                "postgres:15.18",
            ]
        )


def test_reviewed_postgres_init_bind_rejects_read_write_mode() -> None:
    with pytest.raises(DockerLockError, match="read-only"):
        validate(
            [
                "run",
                "--pull=never",
                "-v",
                f"{REPO / 'infra/init'}:/docker-entrypoint-initdb.d",
                "postgres:15.18",
            ]
        )


def test_read_only_compose_config_accepts_regular_contract_files(
    tmp_path: Path,
) -> None:
    overlay = tmp_path / "docker-compose.contract.yml"
    overlay.write_text(
        "services:\n  probe:\n    image: example.invalid/probe:locked\n",
        encoding="utf-8",
    )

    validate(
        [
            "compose",
            "-f",
            str(REPO / "infra/terraform/deploy/docker-compose.aws.yml"),
            "-f",
            str(REPO / "infra/terraform/deploy/docker-compose.cartridges.yml"),
            "-f",
            str(overlay),
            "config",
            "--format",
            "json",
        ]
    )


@pytest.mark.parametrize(
    "tail",
    [
        ["config", "up"],
        ["config", "--format", "json", "up"],
        ["config", "--output", "/tmp/rendered.yml"],
        ["config", "--resolve-image-digests"],
        ["config", "--environment"],
        ["config", "--format", "json", "--quiet", "postgres"],
    ],
)
def test_read_only_config_authority_rejects_mutating_or_unreviewed_tail(
    tmp_path: Path, tail: list[str]
) -> None:
    compose = tmp_path / "docker-compose.yml"
    compose.write_text("services: {}\n", encoding="utf-8")

    with pytest.raises(DockerLockError):
        validate(["compose", "-f", str(compose), *tail])


def test_read_only_config_rejects_symlinked_compose_input(tmp_path: Path) -> None:
    target = tmp_path / "target.yml"
    target.write_text("services: {}\n", encoding="utf-8")
    compose = tmp_path / "docker-compose.yml"
    compose.symlink_to(target)

    with pytest.raises(DockerLockError, match="regular file"):
        validate(["compose", "-f", str(compose), "config", "-q"])


@pytest.mark.parametrize(
    "command",
    [
        [
            "up",
            "-d",
            "--no-build",
            "--pull",
            "never",
            "postgres",
            "postgres_gold",
        ],
        [
            "exec",
            "-T",
            "postgres_gold",
            "psql",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            "postgres",
            "-p",
            "5433",
            "-d",
            "modecissions_gold",
            "-Atc",
            "SELECT 1",
        ],
        [
            "exec",
            "-T",
            "-e",
            "PGOPTIONS=-c app.test=value",
            "postgres",
            "psql",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            "postgres",
            "-d",
            "modecissions",
        ],
        ["down", "-v"],
    ],
)
def test_exact_gold_repair_fixture_commands_pass(
    tmp_path: Path, command: list[str]
) -> None:
    workspace, _root, compose = _gold_sandbox(tmp_path)

    validate(["compose", "-f", str(compose), *command], workspace=workspace)


@pytest.mark.parametrize(
    "command",
    [
        ["up", "-d", "--no-build", "--pull", "never", "postgres"],
        [
            "up",
            "-d",
            "--no-build",
            "--pull",
            "never",
            "postgres_gold",
            "postgres",
        ],
        ["up", "-d", "--no-build", "--pull", "always", "postgres", "postgres_gold"],
        ["exec", "-T", "postgres_gold", "sh", "-c", "true"],
        ["exec", "-T", "console", "psql", "-U", "postgres"],
        ["down"],
        ["down", "-v", "--remove-orphans"],
        ["ps"],
        ["pull"],
        ["build"],
    ],
)
def test_gold_repair_fixture_rejects_every_unreviewed_mutation(
    tmp_path: Path, command: list[str]
) -> None:
    workspace, _root, compose = _gold_sandbox(tmp_path)

    with pytest.raises(DockerLockError):
        validate(["compose", "-f", str(compose), *command], workspace=workspace)


def test_gold_repair_authority_rejects_tampered_bytes(tmp_path: Path) -> None:
    workspace, _root, compose = _gold_sandbox(tmp_path)
    compose.write_text(
        compose.read_text(encoding="utf-8") + "# tampered\n", encoding="utf-8"
    )

    with pytest.raises(DockerLockError, match="bytes"):
        validate(
            [
                "compose",
                "-f",
                str(compose),
                "down",
                "-v",
            ],
            workspace=workspace,
        )


def test_gold_repair_authority_rejects_valid_bytes_at_wrong_path(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "checkout"
    workspace.mkdir()
    wrong = tmp_path / "attacker" / "infra/docker-compose.yml"
    wrong.parent.mkdir(parents=True)
    shutil.copy2(GOLD_FIXTURE, wrong)

    with pytest.raises(DockerLockError, match="path"):
        validate(
            ["compose", "-f", str(wrong), "down", "-v"], workspace=workspace
        )


def test_config_allows_bounded_compose_project_name_environment(
    tmp_path: Path,
) -> None:
    compose = tmp_path / "docker-compose.yml"
    compose.write_text("services: {}\n", encoding="utf-8")
    result, sentinel = _run_guard(
        tmp_path,
        ["compose", "-f", str(compose), "config", "--format", "json"],
        environment={"COMPOSE_PROJECT_NAME": "mcp-pdf-capacity-contract"},
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert sentinel.read_text(encoding="utf-8") == "executed"


def test_config_rejects_invalid_compose_project_name_environment(
    tmp_path: Path,
) -> None:
    compose = tmp_path / "docker-compose.yml"
    compose.write_text("services: {}\n", encoding="utf-8")
    result, sentinel = _run_guard(
        tmp_path,
        ["compose", "-f", str(compose), "config", "-q"],
        environment={"COMPOSE_PROJECT_NAME": "../daemon-control"},
    )

    assert result.returncode == 97
    assert not sentinel.exists()


@pytest.mark.parametrize("project", ["gold-deadbeef", "gold-feedface", "OMEGA"])
def test_gold_repair_project_environment_is_bound_to_fixture_root(
    tmp_path: Path, project: str
) -> None:
    workspace, root, compose = _gold_sandbox(tmp_path)
    result, sentinel = _run_guard(
        tmp_path,
        ["compose", "-f", str(compose), "down", "-v"],
        workspace=workspace,
        environment={"COMPOSE_PROJECT_NAME": project},
    )

    if project == root.name:
        assert result.returncode == 0, result.stdout + result.stderr
        assert sentinel.exists()
    else:
        assert result.returncode == 97
        assert not sentinel.exists()


def test_gold_repair_project_environment_is_mandatory(tmp_path: Path) -> None:
    workspace, _root, compose = _gold_sandbox(tmp_path)
    result, sentinel = _run_guard(
        tmp_path,
        ["compose", "-f", str(compose), "down", "-v"],
        workspace=workspace,
    )

    assert result.returncode == 97
    assert "project name is missing" in result.stderr
    assert not sentinel.exists()


def test_compose_project_name_never_authorizes_non_compose_mutation(
    tmp_path: Path,
) -> None:
    result, sentinel = _run_guard(
        tmp_path,
        ["rm", "-f", "container"],
        environment={"COMPOSE_PROJECT_NAME": "mcp-pdf-capacity-contract"},
    )

    assert result.returncode == 97
    assert not sentinel.exists()


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
            "run",
            "--pull=never",
            "--mount",
            f"type=bind,src={REPO / 'infra/init'},source=/var,"
            "dst=/docker-entrypoint-initdb.d,readonly=true",
            "postgres:15.18",
        ],
        [
            "run",
            "--pull=never",
            "--mount",
            f"type=bind,src={REPO / 'infra/init'},"
            "dst=/docker-entrypoint-initdb.d,readonly=true,ro=false",
            "postgres:15.18",
        ],
        [
            "run",
            "--pull=never",
            "--mount",
            f"type=bind,src={REPO / 'infra/init'},SOURCE=/var,"
            "dst=/docker-entrypoint-initdb.d,readonly=true",
            "postgres:15.18",
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
        "#!/bin/sh\nprintf executed >\"${LOCK_EXEC_SENTINEL}\"\n",
        encoding="utf-8",
    )
    real.chmod(0o755)
    trusted_config = tmp_path / "trusted-docker-config"
    trusted_config.mkdir()
    trusted_config.chmod(0o555)

    result = subprocess.run(
        [
            sys.executable,
            str(REPO / "scripts/release_docker_lock.py"),
            "--workspace",
            str(REPO),
            "--real",
            str(real),
            "--trusted-config",
            str(trusted_config),
            "--",
            *args,
        ],
        cwd=REPO,
        env={**os.environ, "LOCK_EXEC_SENTINEL": str(sentinel)},
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
        "#!/bin/sh\nprintf executed >\"${LOCK_EXEC_SENTINEL}\"\n",
        encoding="utf-8",
    )
    real.chmod(0o755)
    trusted_config = tmp_path / "trusted-docker-config"
    trusted_config.mkdir()
    trusted_config.chmod(0o555)

    result = subprocess.run(
        [
            sys.executable,
            str(REPO / "scripts/release_docker_lock.py"),
            "--workspace",
            str(REPO),
            "--real",
            str(real),
            "--trusted-config",
            str(trusted_config),
            "--",
            "inspect",
            "mode_console",
        ],
        cwd=REPO,
        env={
            **os.environ,
            "LOCK_EXEC_SENTINEL": str(sentinel),
            key: "/tmp/attacker-control",
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 97, result.stdout + result.stderr
    assert not sentinel.exists()


@pytest.mark.parametrize("kind", ("symlink", "writable", "nonempty"))
def test_cli_rejects_untrusted_docker_config_before_exec(
    tmp_path: Path, kind: str
) -> None:
    trusted_config = tmp_path / "trusted-docker-config"
    if kind == "symlink":
        target = tmp_path / "config-target"
        target.mkdir()
        target.chmod(0o555)
        trusted_config.symlink_to(target, target_is_directory=True)
    else:
        trusted_config.mkdir()
        if kind == "nonempty":
            (trusted_config / "config.json").write_text("{}\n", encoding="utf-8")
            trusted_config.chmod(0o555)

    result, sentinel = _run_guard(
        tmp_path,
        ["inspect", "mode_console"],
        trusted_config=trusted_config,
    )

    assert result.returncode == 97, result.stdout + result.stderr
    assert "trusted Docker config directory" in result.stderr
    assert not sentinel.exists()


def test_real_cli_ignores_home_compose_plugin_for_authorized_config(
    tmp_path: Path,
) -> None:
    real = _real_docker_binary()
    trusted_config = tmp_path / "trusted-docker-config"
    trusted_config.mkdir()
    trusted_config.chmod(0o555)
    compose = tmp_path / "docker-compose.yml"
    compose.write_text("services: {}\n", encoding="utf-8")
    attacker_home = tmp_path / "attacker-home"
    plugin = attacker_home / ".docker/cli-plugins/docker-compose"
    plugin.parent.mkdir(parents=True)
    sentinel = tmp_path / "attacker-plugin-executed"
    plugin.write_text(
        "#!/bin/sh\nprintf compromised >\"${PLUGIN_SENTINEL}\"\nexit 33\n",
        encoding="utf-8",
    )
    plugin.chmod(0o755)

    baseline = subprocess.run(
        [
            str(real),
            "--config",
            str(trusted_config),
            "compose",
            "-f",
            str(compose),
            "config",
            "-q",
        ],
        env=_docker_test_environment(),
        text=True,
        capture_output=True,
        check=False,
    )

    result = subprocess.run(
        [
            sys.executable,
            str(REPO / "scripts/release_docker_lock.py"),
            "--workspace",
            str(REPO),
            "--real",
            str(real),
            "--trusted-config",
            str(trusted_config),
            "--",
            "compose",
            "-f",
            str(compose),
            "config",
            "-q",
        ],
        cwd=REPO,
        env=_docker_test_environment(
            HOME=str(attacker_home),
            XDG_CONFIG_HOME=str(attacker_home / "xdg"),
            PLUGIN_SENTINEL=str(sentinel),
        ),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == baseline.returncode
    assert result.stdout == baseline.stdout
    assert result.stderr == baseline.stderr
    assert not sentinel.exists()


def test_real_cli_ignores_home_current_context_for_daemon_command(
    tmp_path: Path,
) -> None:
    real = _real_docker_binary()
    trusted_config = tmp_path / "trusted-docker-config"
    trusted_config.mkdir()
    trusted_config.chmod(0o555)
    baseline = subprocess.run(
        [str(real), "--config", str(trusted_config), "info", "--format", "{{.Name}}"],
        env=_docker_test_environment(),
        text=True,
        capture_output=True,
        check=False,
    )
    attacker_home = tmp_path / "attacker-home"
    docker_home = attacker_home / ".docker"
    context_id = hashlib.sha256(b"attacker").hexdigest()
    context_meta = docker_home / "contexts/meta" / context_id / "meta.json"
    context_meta.parent.mkdir(parents=True)
    (docker_home / "config.json").write_text(
        '{"currentContext":"attacker"}\n', encoding="utf-8"
    )
    context_meta.write_text(
        '{"Name":"attacker","Metadata":{},"Endpoints":{"docker":'
        '{"Host":"tcp://127.0.0.1:1","SkipTLSVerify":false}}}\n',
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(REPO / "scripts/release_docker_lock.py"),
            "--workspace",
            str(REPO),
            "--real",
            str(real),
            "--trusted-config",
            str(trusted_config),
            "--",
            "info",
            "--format",
            "{{.Name}}",
        ],
        cwd=REPO,
        env=_docker_test_environment(
            HOME=str(attacker_home),
            XDG_CONFIG_HOME=str(attacker_home / "xdg"),
        ),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == baseline.returncode
    assert result.stdout == baseline.stdout
    assert result.stderr == baseline.stderr
    assert "127.0.0.1:1" not in result.stdout + result.stderr
