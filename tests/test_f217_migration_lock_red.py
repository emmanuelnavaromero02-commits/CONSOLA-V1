from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
MIGRATION_SCRIPT = REPO / "scripts/apply_db_migrations.sh"


@dataclass(frozen=True)
class MigrationRuntime:
    workspace: Path
    environment: dict[str, str]
    calls: Path


def _write_executable(path: Path, source: str) -> None:
    path.write_text(source, encoding="utf-8")
    path.chmod(0o755)


def _migration_runtime(
    tmp_path: Path,
    *,
    model_non_control_local: bool = False,
) -> MigrationRuntime:

    workspace = tmp_path / "checkout"
    (workspace / "scripts").mkdir(parents=True)
    (workspace / "infra/init").mkdir(parents=True)
    (workspace / "infra/init_gold").mkdir(parents=True)
    shutil.copy2(REPO / "scripts/load_release_dotenv.py", workspace / "scripts")
    shutil.copy2(REPO / "infra/docker-compose.yml", workspace / "infra")
    (workspace / "infra/.env").write_text("TEST_ONLY=1\n", encoding="utf-8")
    (workspace / "infra/init/01_red.sql").write_text("SELECT 1;\n", encoding="utf-8")
    (workspace / "infra/init_gold/01_red.sql").write_text(
        "SELECT 1;\n", encoding="utf-8"
    )

    migration_source = MIGRATION_SCRIPT.read_text(encoding="utf-8")
    if model_non_control_local:
        old_assignment = 'COMPOSE_FILE="${ROOT_DIR}/infra/docker-compose.yml"'
        new_assignment = 'MIGRATION_COMPOSE_PATH="${ROOT_DIR}/infra/docker-compose.yml"'
        if old_assignment in migration_source:
            assert migration_source.count(old_assignment) == 1
            assert migration_source.count('"${COMPOSE_FILE}"') == 2
            migration_source = migration_source.replace(old_assignment, new_assignment)
            migration_source = migration_source.replace(
                '"${COMPOSE_FILE}"', '"${MIGRATION_COMPOSE_PATH}"'
            )
        else:
            assert migration_source.count(new_assignment) == 1
            assert migration_source.count('"${MIGRATION_COMPOSE_PATH}"') == 2
    migration = workspace / "scripts/apply_db_migrations.sh"
    _write_executable(migration, migration_source)

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    calls = tmp_path / "docker.calls"
    real_docker = tmp_path / "docker-real"
    _write_executable(
        real_docker,
        """#!/usr/bin/env bash
set -euo pipefail
[[ "${1:-}" == "--config" && "$#" -ge 3 ]] || exit 68
shift 2
printf '%s\n' "$*" >> "${FAKE_DOCKER_CALLS}"
""",
    )

    trusted_config = tmp_path / "trusted-docker-config"
    trusted_config.mkdir()
    trusted_config.chmod(0o555)
    _write_executable(
        fake_bin / "docker",
        """#!/usr/bin/env bash
exec "${LOCK_TEST_PYTHON}" "${LOCK_TEST_SCRIPT}" \
  --workspace "${LOCK_TEST_WORKSPACE}" \
  --real "${LOCK_TEST_REAL_DOCKER}" \
  --trusted-config "${LOCK_TEST_DOCKER_CONFIG}" -- "$@"
""",
    )

    clean_environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("COMPOSE_", "DOCKER_"))
    }
    environment = {
        **clean_environment,
        "BASH_ENV": "/dev/null",
        "ENV": "/dev/null",
        "FAKE_DOCKER_CALLS": str(calls),
        "LD_AUDIT": "",
        "LD_LIBRARY_PATH": "",
        "LD_PRELOAD": "",
        "LOCK_TEST_DOCKER_CONFIG": str(trusted_config),
        "LOCK_TEST_PYTHON": sys.executable,
        "LOCK_TEST_REAL_DOCKER": str(real_docker),
        "LOCK_TEST_SCRIPT": str(REPO / "scripts/release_docker_lock.py"),
        "LOCK_TEST_WORKSPACE": str(workspace),
        "OMEGA_MIGRATION_LOCK_FILE": str(tmp_path / "migration.lock"),
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "PYTHONHOME": "",
        "PYTHONPATH": "",
    }
    return MigrationRuntime(workspace, environment, calls)


def _run_migrations(
    runtime: MigrationRuntime,
    *,
    compose_file: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "scripts/apply_db_migrations.sh"],
        cwd=runtime.workspace,
        env={**runtime.environment, "COMPOSE_FILE": compose_file},
        text=True,
        capture_output=True,
        check=False,
    )


def _run_with_control_environment(
    runtime: MigrationRuntime,
    **control_environment: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "scripts/apply_db_migrations.sh"],
        cwd=runtime.workspace,
        env={**runtime.environment, **control_environment},
        text=True,
        capture_output=True,
        check=False,
    )


def test_exported_empty_compose_file_reaches_migrations_through_real_lock(
    tmp_path: Path,
) -> None:

    runtime = _migration_runtime(tmp_path)
    result = _run_migrations(runtime, compose_file="")

    assert result.returncode == 0, (
        "exported COMPOSE_FILE='' was reassigned and leaked into the lock:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "[migrate] done" in result.stdout
    assert runtime.calls.is_file(), "migrations never reached the fake daemon"
    calls = runtime.calls.read_text(encoding="utf-8").splitlines()
    assert len(calls) >= 8
    compose_path = runtime.workspace / "infra/docker-compose.yml"
    assert all(
        line.startswith(f"compose -f {compose_path} exec -T -e PGOPTIONS=")
        for line in calls
    )
    assert any(" postgres psql " in line for line in calls)
    assert any(" postgres_gold psql " in line for line in calls)


def test_migration_compose_path_is_a_non_control_local() -> None:
    source = MIGRATION_SCRIPT.read_text(encoding="utf-8")

    assert 'MIGRATION_COMPOSE_PATH="${ROOT_DIR}/infra/docker-compose.yml"' in source
    assert 'COMPOSE_FILE="${ROOT_DIR}/infra/docker-compose.yml"' not in source
    assert source.count('"${MIGRATION_COMPOSE_PATH}"') == 2
    assert "${COMPOSE_FILE+x} == x && -z ${COMPOSE_FILE}" in source


def test_non_control_local_is_sufficient_for_green_migration_acceptance(
    tmp_path: Path,
) -> None:

    runtime = _migration_runtime(tmp_path, model_non_control_local=True)
    result = _run_migrations(runtime, compose_file="")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "[migrate] done" in result.stdout
    assert runtime.calls.is_file()


def test_real_compose_poison_still_returns_97_without_daemon_access(
    tmp_path: Path,
) -> None:

    runtime = _migration_runtime(tmp_path, model_non_control_local=True)
    result = _run_migrations(
        runtime,
        compose_file="/tmp/attacker-compose.yml",
    )
    combined = result.stdout + result.stderr

    assert result.returncode == 97, combined
    assert "RELEASE DOCKER LOCK BLOCKED" in combined
    assert (
        "Docker control environment is forbidden after release lock: COMPOSE_FILE"
        in combined
    )
    assert not runtime.calls.exists(), "real poison reached the fake Docker daemon"


@pytest.mark.parametrize(
    ("key", "value"),
    (
        ("COMPOSE_FILE", "/tmp/attacker-compose.yml"),
        ("COMPOSE_PROFILES", "attacker"),
        ("COMPOSE_PROJECT_NAME", "attacker"),
        ("DOCKER_HOST", "tcp://attacker.invalid:2375"),
        ("DOCKER_CONTEXT", "attacker"),
    ),
)
def test_every_real_nonempty_control_poison_remains_rc97_without_daemon_access(
    tmp_path: Path,
    key: str,
    value: str,
) -> None:
    runtime = _migration_runtime(tmp_path)
    result = _run_with_control_environment(runtime, **{key: value})

    assert result.returncode == 97, result.stdout + result.stderr
    assert "RELEASE DOCKER LOCK BLOCKED" in result.stderr
    assert key in result.stderr or (
        key == "COMPOSE_PROJECT_NAME" and "project name" in result.stderr
    )
    assert not runtime.calls.exists(), "poison reached the fake Docker daemon"
