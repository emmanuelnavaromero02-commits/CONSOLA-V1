from __future__ import annotations

from pathlib import Path

import pytest

from tests.ci_gate_contract_helpers import (
    assert_gate_rejects,
    detector_outputs,
    load_job,
    run_gate,
)


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


def test_detector_classifies_its_own_path_as_infra_only() -> None:
    outputs = detector_outputs("scripts/ci_changed_areas.py")
    assert {
        name: outputs[name]
        for name in ("python_runtime", "python_deps", "node_deps", "infra")
    } == {
        "python_runtime": "false",
        "python_deps": "false",
        "node_deps": "false",
        "infra": "true",
    }


def test_detector_change_requires_all_security_scanners(tmp_path: Path) -> None:
    result = run_gate(SECURITY_SCRIPT, PROTECTED_ENV, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


def test_both_bandit_commands_scan_the_detector() -> None:
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
