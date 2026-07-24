from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tests.ci_gate_contract_helpers import (
    assert_gate_rejects,
    detector_outputs,
    load_job,
    run_gate,
)


ROOT = Path(__file__).resolve().parents[1]
DETECTOR = ROOT / "scripts/ci_changed_areas.py"
SECURITY_JOB = load_job("security.yml", "security-gate")
SECURITY_SCRIPT = SECURITY_JOB["steps"][0]["run"]
PROTECTED_ENV = {
    "CHANGES_RESULT": "success",
    "PYTHON_RUNTIME": "false",
    "PYTHON_DEPS": "false",
    "NODE_DEPS": "false",
    "INFRA": "true",
    "BANDIT_RESULT": "success",
    "PIP_AUDIT_RESULT": "success",
    "NPM_AUDIT_RESULT": "success",
    "NO_SECURITY_SCAN_NEEDED_RESULT": "skipped",
}


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def _init_repo(repo: Path) -> None:
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "CI")
    _git(repo, "config", "user.email", "ci@example.test")


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


@pytest.mark.parametrize(
    "path",
    [
        "scripts/ci_changed_areas.py",
        "scripts/ci_control_room_paths.py",
        ".github/workflows/control-room-postgres-rls.yml",
        "tests/ci_gate_contract_helpers.py",
        "tests/test_control_room_gate_contract.py",
    ],
)
def test_detector_classifies_protected_paths_as_infra_control_room(path: str) -> None:
    outputs = detector_outputs(path)
    assert {
        name: outputs[name]
        for name in (
            "python_runtime",
            "python_deps",
            "node_deps",
            "infra",
            "control_room",
        )
    } == {
        "python_runtime": "false",
        "python_deps": "false",
        "node_deps": "false",
        "infra": (
            "true" if path.startswith(("scripts/", ".github/workflows/")) else "false"
        ),
        "control_room": "true",
    }


def test_detector_change_requires_all_security_scanners(tmp_path: Path) -> None:
    result = run_gate(SECURITY_SCRIPT, PROTECTED_ENV, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


def test_both_bandit_commands_scan_both_detectors() -> None:
    bandit_job = load_job("security.yml", "bandit")
    script = next(
        step["run"]
        for step in bandit_job["steps"]
        if "bandit -r" in step.get("run", "")
    )
    commands = script.split("bandit -r")[1:]
    assert len(commands) == 2
    for command in commands:
        targets = command.split("--severity-level", 1)[0]
        assert "scripts/ci_changed_areas.py" in targets
        assert "scripts/ci_control_room_paths.py" in targets


def test_changed_path_cannot_inject_github_output(tmp_path: Path) -> None:
    repo = tmp_path / "output-injection"
    _init_repo(repo)
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    base = _commit(repo, "base")
    name = "cartridges/evil\ncontrol_room<<EOF\nfalse\nEOF\nx=/requirements.txt"
    changed = repo / name
    changed.parent.mkdir(parents=True)
    changed.write_text("unsafe\n", encoding="utf-8")
    head = _commit(repo, "unsafe path")
    output = tmp_path / "github-output"

    result = subprocess.run(
        ["python3", str(DETECTOR), "--base", base, "--head", head],
        cwd=repo,
        env={**os.environ, "GITHUB_OUTPUT": str(output)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert not output.exists()


def test_non_linear_push_fails_before_writing_outputs(tmp_path: Path) -> None:
    repo = tmp_path / "non-linear"
    _init_repo(repo)
    protected = repo / "console/app/main.py"
    protected.parent.mkdir(parents=True)
    protected.write_text("protected\n", encoding="utf-8")
    root = _commit(repo, "root")
    protected.unlink()
    before = _commit(repo, "old history")
    _git(repo, "checkout", "-q", "-b", "rewritten", root)
    (repo / "README.md").write_text("unrelated\n", encoding="utf-8")
    head = _commit(repo, "new history")
    output = tmp_path / "push-output"

    result = subprocess.run(
        ["python3", str(DETECTOR)],
        cwd=repo,
        env={
            **os.environ,
            "GITHUB_EVENT_NAME": "push",
            "GITHUB_EVENT_BEFORE": before,
            "GITHUB_SHA": head,
            "GITHUB_OUTPUT": str(output),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert not output.exists()


@pytest.mark.parametrize(
    "scanner",
    ["BANDIT_RESULT", "PIP_AUDIT_RESULT", "NPM_AUDIT_RESULT"],
)
@pytest.mark.parametrize("result", ["skipped", "failure", "cancelled"])
def test_detector_change_fails_closed_when_scanner_does_not_succeed(
    scanner: str,
    result: str,
    tmp_path: Path,
) -> None:
    env = {**PROTECTED_ENV, scanner: result}
    assert_gate_rejects(run_gate(SECURITY_SCRIPT, env, tmp_path))
