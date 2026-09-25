from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from scripts.load_release_dotenv import DotenvError, parse


REPO = Path(__file__).resolve().parents[1]


def test_release_dotenv_is_data_not_shell_code() -> None:
    assert parse('NAME="System Administrator"\nHARMLESS=1 exit 0\n') == {
        "NAME": "System Administrator",
        "HARMLESS": "1 exit 0",
    }


@pytest.mark.parametrize(
    "text",
    ("exit 0\n", "A=1\nA=2\n", 'A="unterminated\n'),
)
def test_release_dotenv_rejects_non_assignments_duplicates_and_bad_quotes(
    text: str,
) -> None:
    with pytest.raises(DotenvError):
        parse(text)


@pytest.mark.parametrize(
    "key",
    ("LD_PRELOAD", "LD_LIBRARY_PATH", "LD_AUDIT", "LD_FUTURE_CONTROL"),
)
def test_release_dotenv_reserves_every_dynamic_loader_variable(key: str) -> None:
    with pytest.raises(DotenvError, match=rf"reserved dotenv variable {key}"):
        parse(f"{key}=/attacker-controlled.so\n")


@pytest.mark.parametrize(
    "key",
    (
        "OMEGA_GCP_IMAGE_CONSOLE",
        "OMEGA_REQUIRE_SUPERSET_LOGIN",
        "OMEGA_REQUIRE_LIVE_LLM",
        "OMEGA_ENABLE_E2E_SMOKE",
        "OMEGA_ENABLE_LIVE_STACK_TESTS",
        "OMEGA_PRODUCTION_READINESS_SKIP_STRESS",
        "OMEGA_WAIT_FULL_STACK",
        "E2E_REQUIRE_STACK",
        "GIT_DIR",
        "GIT_INDEX_FILE",
        "NODE_PATH",
    ),
)
def test_release_dotenv_cannot_override_release_authority_or_gates(key: str) -> None:
    with pytest.raises(DotenvError, match=rf"reserved dotenv variable {key}"):
        parse(f"{key}=attacker-controlled\n")


def test_production_readiness_dotenv_consumers_never_source_env_files() -> None:
    for relative in (
        "scripts/apply_db_migrations.sh",
        "scripts/run_multiuser_isolation_simulation.sh",
    ):
        script = (REPO / relative).read_text(encoding="utf-8")
        assert "source infra/.env" not in script
        assert 'source "${ENV_FILE}"' not in script
        assert ". infra/.env" not in script
        assert "load_release_dotenv.py" in script

    for relative in ("scripts/production_readiness.sh", "scripts/run-e2e.sh"):
        script = (REPO / relative).read_text(encoding="utf-8")
        assert "LD_[A-Za-z0-9_]*" in script


def _executable(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


def test_migration_runner_treats_dotenv_as_data_before_executable_sentinel(
    tmp_path: Path,
) -> None:
    (tmp_path / "scripts").mkdir()
    (tmp_path / "infra").mkdir()
    (tmp_path / "fake-bin").mkdir()
    shutil.copy2(REPO / "scripts/apply_db_migrations.sh", tmp_path / "scripts")
    shutil.copy2(REPO / "scripts/load_release_dotenv.py", tmp_path / "scripts")
    (tmp_path / "infra/.env").write_text("HARMLESS=1 exit 0\n", encoding="utf-8")
    sentinel = tmp_path / "migration-work-started"
    _executable(
        tmp_path / "fake-bin/docker",
        '#!/usr/bin/env bash\nprintf "%s\\n" called >"${OMEGA_TEST_SENTINEL}"\nexit 73\n',
    )

    result = subprocess.run(
        ["bash", str(tmp_path / "scripts/apply_db_migrations.sh")],
        cwd=tmp_path,
        env={
            **os.environ,
            "PATH": f"{tmp_path / 'fake-bin'}:{os.environ['PATH']}",
            "OMEGA_MIGRATION_LOCK_FILE": str(tmp_path / "migration.lock"),
            "OMEGA_TEST_SENTINEL": str(sentinel),
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 73, result.stdout + result.stderr
    assert sentinel.read_text(encoding="utf-8") == "called\n"


def test_multiuser_runner_treats_dotenv_as_data_before_executable_sentinel(
    tmp_path: Path,
) -> None:
    (tmp_path / "scripts").mkdir()
    (tmp_path / "infra").mkdir()
    shutil.copy2(
        REPO / "scripts/run_multiuser_isolation_simulation.sh", tmp_path / "scripts"
    )
    shutil.copy2(REPO / "scripts/load_release_dotenv.py", tmp_path / "scripts")
    (tmp_path / "infra/.env").write_text("HARMLESS=1 exit 0\n", encoding="utf-8")
    sentinel = tmp_path / "multiuser-work-started"
    runner = tmp_path / "simulation-sentinel"
    _executable(
        runner,
        '#!/usr/bin/env bash\nprintf "%s\\n" called >"${OMEGA_TEST_SENTINEL}"\nexit 73\n',
    )

    result = subprocess.run(
        ["bash", str(tmp_path / "scripts/run_multiuser_isolation_simulation.sh")],
        cwd=tmp_path,
        env={
            **os.environ,
            "DATABASE_URL": "postgresql://inherited-authority.invalid/db",
            "OMEGA_TEST_SENTINEL": str(sentinel),
            "PYTHON_BIN": str(runner),
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 73, result.stdout + result.stderr
    assert sentinel.read_text(encoding="utf-8") == "called\n"
