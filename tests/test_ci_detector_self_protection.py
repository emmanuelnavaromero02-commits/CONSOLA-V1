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
        "tests/test_control_room_path_policy.py",
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
        assert "scripts/gcp/generate_release_authority.py" in targets
        assert "scripts/gcp/iam_revoke_transaction.py" in targets
        assert "scripts/gcp/startup_metadata_transaction.py" in targets
        assert "scripts/gcp/terraform_plan_contract.py" in targets
        assert "scripts/gcp/terraform_transaction.py" in targets
        assert "scripts/gcp/verify_edge_tls.py" in targets
        assert "scripts/gcp/verify_terraform_plan.py" in targets


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


def test_changed_test_path_cannot_inject_pytest_arguments(tmp_path: Path) -> None:
    repo = tmp_path / "pytest-argument-injection"
    _init_repo(repo)
    name = "tests/test_victim.py --ignore tests/test_victim.py"
    changed = repo / name
    changed.parent.mkdir(parents=True)
    changed.write_text("def test_placeholder(): pass\n", encoding="utf-8")
    output = tmp_path / "github-output"

    result = subprocess.run(
        ["python3", str(DETECTOR), "--files", name],
        cwd=repo,
        env={**os.environ, "GITHUB_OUTPUT": str(output)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "unsafe whitespace" in result.stderr
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


def test_lint_workflow_runs_pinned_opentofu_validation_for_infra() -> None:
    changes = load_job("lint.yml", "changes")
    assert changes["outputs"]["infra"] == "${{ steps.detect.outputs.infra }}"
    assert changes["steps"][0]["uses"] == (
        "actions/checkout@" "11bd71901bbe5b1630ceea73d27597364c9af683"
    )

    job = load_job("lint.yml", "gcp-host-foundation")
    assert job["needs"] == "changes"
    assert job["if"] == "needs.changes.outputs.infra == 'true'"
    assert job["runs-on"] == "ubuntu-latest"
    checkout = next(
        step
        for step in job["steps"]
        if str(step.get("uses", "")).startswith("actions/checkout@")
    )
    assert checkout["uses"] == (
        "actions/checkout@" "11bd71901bbe5b1630ceea73d27597364c9af683"
    )
    setup = next(
        step
        for step in job["steps"]
        if str(step.get("uses", "")).startswith("opentofu/setup-opentofu@")
    )
    assert setup["uses"] == (
        "opentofu/setup-opentofu@" "a1320f892987e89d278cc92dc5adc984fb93aca4"
    )
    assert setup["with"] == {
        "tofu_version": "1.11.6",
        "tofu_wrapper": False,
    }
    commands = "\n".join(step.get("run", "") for step in job["steps"] if "run" in step)
    assert "bash -n scripts/gcp/*.sh" in commands
    assert "dash -n infra/terraform-gcp/templates/omega-operation-gate" in commands
    assert "shellcheck=0.10.0-1" in commands
    assert "tests/test_gcp_edge_tls.py" in commands
    assert "tests/test_gcp_ghcr_private_auth.py" in commands
    assert "tests/test_gcp_iam_revoke_transaction.py" in commands
    assert "tests/test_gcp_release_authority.py" in commands
    assert "tests/test_gcp_startup_metadata_transaction.py" in commands
    assert "tests/test_gcp_terraform_plan.py" in commands
    assert "tests/test_gcp_terraform_transaction.py" in commands
    assert "tests/test_lint_gate_contract.py" in commands
    assert "scripts/gcp/generate_release_authority.py" in commands
    assert "scripts/gcp/iam_revoke_transaction.py" in commands
    assert "scripts/gcp/startup_metadata_transaction.py" in commands
    assert "scripts/gcp/terraform_plan_contract.py" in commands
    assert "scripts/gcp/terraform_transaction.py" in commands
    assert "scripts/gcp/verify_terraform_plan.py" in commands
    assert "ruff format --check" in commands
    assert "tofu -chdir=infra/terraform-gcp fmt -check -recursive" in commands
    assert (
        "tofu -chdir=infra/terraform-gcp init -backend=false -lockfile=readonly"
        in commands
    )
    assert "tofu -chdir=infra/terraform-gcp validate" in commands


def test_foundation_operator_surfaces_are_self_protected_by_change_detector() -> None:
    detector = DETECTOR.read_text(encoding="utf-8")
    for path in (
        'r"^Makefile$"',
        'r"^docs/runbook/16_gcp_canonical_day2_release[.]md$"',
    ):
        assert detector.count(path) >= 2
    for contract in (
        "tests/test_gcp_startup_metadata_transaction.py",
        "tests/test_gcp_terraform_transaction.py",
    ):
        assert contract in detector


def _lint_gate_env(**overrides: str) -> dict[str, str]:
    values = {
        "CHANGES_RESULT": "success",
        "PYTHON": "false",
        "FRONTEND": "false",
        "INFRA": "true",
        "RUFF_RESULT": "skipped",
        "CONSOLE_NEXT_RESULT": "skipped",
        "GCP_HOST_FOUNDATION_RESULT": "success",
        "NO_LINT_NEEDED_RESULT": "skipped",
    }
    values.update(overrides)
    return values


def test_lint_gate_requires_successful_gcp_host_validation(tmp_path: Path) -> None:
    gate = load_job("lint.yml", "lint-gate")
    assert "gcp-host-foundation" in gate["needs"]
    step = gate["steps"][0]
    assert step["env"]["INFRA"] == "${{ needs.changes.outputs.infra }}"
    assert step["env"]["GCP_HOST_FOUNDATION_RESULT"] == (
        "${{ needs['gcp-host-foundation'].result }}"
    )
    result = run_gate(step["run"], _lint_gate_env(), tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("result", ["skipped", "failure", "cancelled", ""])
def test_lint_gate_fails_closed_when_gcp_host_validation_is_not_success(
    result: str,
    tmp_path: Path,
) -> None:
    script = load_job("lint.yml", "lint-gate")["steps"][0]["run"]
    actual = run_gate(
        script,
        _lint_gate_env(GCP_HOST_FOUNDATION_RESULT=result),
        tmp_path,
    )
    assert_gate_rejects(actual)


@pytest.mark.parametrize("infra", ["", "yes", "TRUE", "0"])
def test_lint_gate_rejects_non_boolean_infra_output(
    infra: str,
    tmp_path: Path,
) -> None:
    script = load_job("lint.yml", "lint-gate")["steps"][0]["run"]
    actual = run_gate(script, _lint_gate_env(INFRA=infra), tmp_path)
    assert_gate_rejects(actual)
