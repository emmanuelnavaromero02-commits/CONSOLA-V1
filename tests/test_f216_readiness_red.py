"""RED acceptance contracts for the v1.45.216 readiness recovery."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
WORKFLOW = REPO / ".github/workflows/release.yml"
RELEASE_COMPOSE_FILES = (
    "infra/docker-compose.yml",
    "infra/docker-compose.dev.yml",
    "infra/terraform-gcp/release/docker-compose.release.yml",
)


@dataclass(frozen=True)
class LockedRuntime:
    workspace: Path
    environment: dict[str, str]
    calls: Path


def _jobs() -> dict[str, object]:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return workflow["jobs"]


def _named_step(job: dict[str, object], name: str) -> dict[str, object]:
    return next(step for step in job["steps"] if step.get("name") == name)


def _locked_runtime(tmp_path: Path) -> LockedRuntime:
    workspace = tmp_path / "checkout"
    for relative in RELEASE_COMPOSE_FILES:
        target = workspace / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / relative, target)
    (workspace / "infra/.env").write_text("TEST_ONLY=1\n", encoding="utf-8")
    wait_script = workspace / "scripts/wait_for_health.sh"
    wait_script.parent.mkdir(parents=True)
    shutil.copy2(REPO / "scripts/wait_for_health.sh", wait_script)

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    calls = tmp_path / "docker.calls"
    real_docker = tmp_path / "docker-real"
    real_docker.write_text(
        """#!/usr/bin/env bash
set -u
if [[ "${1:-}" == "--config" ]]; then
  [[ "$#" -ge 3 ]] || exit 68
  shift 2
fi
command="${1:-}"
shift || true
last=""
if [[ "$#" -gt 0 ]]; then
  last="${!#}"
fi
printf 'command=%s argc=%s last=%s\n' \
  "${command}" "$#" "${last}" >> "${FAKE_DOCKER_CALLS}"
case "${command}" in
  inspect)
    if [[ "${FAKE_HEALTH_MODE:-healthy}" == "healthy" ]]; then
      printf 'healthy\n'
    else
      printf 'unhealthy\n'
    fi
    [[ "${FAKE_INSPECT_FAIL_FOR:-}" != "${last}" ]] || exit 71
    ;;
  ps)
    # No restarting containers.
    ;;
  compose)
    if [[ " $* " == *" ps -aq "* ]]; then
      printf '%s' "${FAKE_COMPOSE_IDS:-}"
    elif [[ " $* " == *" ps --all --no-trunc "* ]]; then
      printf 'compose diagnostics\n'
      exit "${FAKE_DIAGNOSTIC_PS_RC:-0}"
    else
      exit 72
    fi
    ;;
  logs)
    printf 'logs for %s\n' "${last}"
    [[ "${FAKE_LOG_FAIL_FOR:-}" != "${last}" ]] || exit 73
    ;;
  *)
    exit 74
    ;;
esac
""",
        encoding="utf-8",
    )
    real_docker.chmod(0o755)

    trusted_config = tmp_path / "trusted-docker-config"
    trusted_config.mkdir()
    trusted_config.chmod(0o555)
    locked_docker = fake_bin / "docker"
    locked_docker.write_text(
        """#!/usr/bin/env bash
exec "${LOCK_TEST_PYTHON}" "${LOCK_TEST_SCRIPT}" \
  --workspace "${LOCK_TEST_WORKSPACE}" \
  --real "${LOCK_TEST_REAL_DOCKER}" \
  --trusted-config "${LOCK_TEST_DOCKER_CONFIG}" -- "$@"
""",
        encoding="utf-8",
    )
    locked_docker.chmod(0o755)
    fake_curl = fake_bin / "curl"
    fake_curl.write_text(
        '#!/usr/bin/env bash\nexit "${FAKE_CURL_RC:-0}"\n',
        encoding="utf-8",
    )
    fake_curl.chmod(0o755)

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
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "PYTHONHOME": "",
        "PYTHONPATH": "",
    }
    return LockedRuntime(workspace, environment, calls)


def _run_wait(
    runtime: LockedRuntime,
    *,
    compose_file: str | None,
    health: str = "healthy",
    normalize_empty: bool = False,
) -> subprocess.CompletedProcess[str]:
    environment = {
        **runtime.environment,
        "FAKE_HEALTH_MODE": health,
        "OMEGA_WAIT_FULL_STACK": "1",
        "WAIT_READY_STREAK": "1",
        "WAIT_SLEEP_SECONDS": "0.05",
        "WAIT_TIMEOUT_SECONDS": "2",
    }
    if compose_file is not None:
        environment["COMPOSE_FILE"] = compose_file
    command = ["bash", "scripts/wait_for_health.sh"]
    if normalize_empty:
        command = [
            "bash",
            "-c",
            (
                "[[ -n ${COMPOSE_FILE:-} ]] || unset COMPOSE_FILE; "
                "exec bash scripts/wait_for_health.sh"
            ),
        ]
    return subprocess.run(
        command,
        cwd=runtime.workspace,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


def test_fake_healthy_daemon_reaches_ready_when_control_variable_is_absent(
    tmp_path: Path,
) -> None:
    """Prove the real lock and fake daemon form a valid GREEN control."""

    runtime = _locked_runtime(tmp_path)
    result = _run_wait(runtime, compose_file=None)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "[wait_for_health] Stack ready." in result.stdout
    assert "=starting" not in result.stdout
    recorded = runtime.calls.read_text(encoding="utf-8")
    assert "command=inspect" in recorded
    assert "command=ps" in recorded


def test_normalizing_exported_empty_control_is_sufficient_for_green_readiness(
    tmp_path: Path,
) -> None:
    """Model the minimal fix and prove it reaches the healthy fake daemon."""

    runtime = _locked_runtime(tmp_path)
    result = _run_wait(runtime, compose_file="", normalize_empty=True)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "[wait_for_health] Stack ready." in result.stdout
    assert "=starting" not in result.stdout
    recorded = runtime.calls.read_text(encoding="utf-8")
    assert "command=inspect" in recorded
    assert "command=ps" in recorded


def test_exported_empty_compose_file_reaches_ready_through_real_release_lock(
    tmp_path: Path,
) -> None:
    """An empty workflow control variable must remain empty in Docker children."""

    runtime = _locked_runtime(tmp_path)
    result = _run_wait(runtime, compose_file="")

    assert result.returncode == 0, (
        "exported COMPOSE_FILE='' poisoned healthy Docker inspection:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "[wait_for_health] Stack ready." in result.stdout
    assert "=starting" not in result.stdout
    recorded = runtime.calls.read_text(encoding="utf-8")
    assert "command=inspect" in recorded
    assert "command=ps" in recorded


def test_nonempty_compose_file_poison_remains_fail_closed(
    tmp_path: Path,
) -> None:
    runtime = _locked_runtime(tmp_path)
    result = _run_wait(runtime, compose_file="/tmp/attacker-compose.yml")
    combined = result.stdout + result.stderr

    assert result.returncode == 97
    assert "[wait_for_health] Stack ready." not in result.stdout
    assert "=starting" not in combined
    assert "[wait_for_health] ERROR" not in combined
    assert "RELEASE DOCKER LOCK BLOCKED" in combined
    assert "COMPOSE_FILE" in combined
    assert not runtime.calls.exists(), "poison reached the fake Docker daemon"


def test_failure_diagnostics_are_best_effort_without_masking_release_failure(
    tmp_path: Path,
) -> None:
    runtime = _locked_runtime(tmp_path)
    readiness = _run_wait(runtime, compose_file=None, health="unhealthy")
    assert readiness.returncode != 0

    if runtime.calls.exists():
        runtime.calls.unlink()
    job = _jobs()["digest-full-stack-gate"]
    diagnostic = _named_step(job, "Digest-stack logs on failure")
    publisher = _jobs()["publish-release-manifest"]
    result = subprocess.run(
        ["bash", "-c", diagnostic["run"]],
        cwd=runtime.workspace,
        env={
            **runtime.environment,
            "COMPOSE_FILE": "",
            "COMPOSE_PROFILES": "",
            "COMPOSE_PROJECT_NAME": "",
            "DOCKER_CONTEXT": "",
            "DOCKER_HOST": "",
            "FAKE_COMPOSE_IDS": "container-b\ncontainer-a\ncontainer-b\n",
            "FAKE_DIAGNOSTIC_PS_RC": "65",
            "FAKE_INSPECT_FAIL_FOR": "container-b",
            "FAKE_LOG_FAIL_FOR": "container-a",
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    recorded = runtime.calls.read_text(encoding="utf-8").splitlines()
    assert [line for line in recorded if line.startswith("command=inspect ")] == [
        "command=inspect argc=5 last=container-a",
        "command=inspect argc=5 last=container-b",
    ]
    assert [line for line in recorded if line.startswith("command=logs ")] == [
        "command=logs argc=3 last=container-a",
        "command=logs argc=3 last=container-b",
    ]
    assert diagnostic["if"] == "failure()"
    assert "needs.digest-full-stack-gate.result == 'success'" in publisher["if"]
    assert readiness.returncode != 0, "successful diagnostics masked readiness"
