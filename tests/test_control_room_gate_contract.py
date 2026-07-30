from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tests.ci_gate_contract_helpers import (
    assert_gate_rejects,
    assert_gate_shape,
    count_job_name,
    load_job,
    load_workflow,
    run_gate,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/ci_changed_areas.py"
WORKFLOW = load_workflow("control-room-postgres-rls.yml")
GATE = load_job("control-room-postgres-rls.yml", "control-room-gate")
ENV_BINDINGS = {
    "CHANGES_RESULT": "${{ needs.changes.result }}",
    "CONTROL_ROOM": "${{ needs.changes.outputs.control_room }}",
    "CONTROL_ROOM_TESTS_RESULT": "${{ needs['control-room-postgres-rls'].result }}",
    "OPERATIONAL_TRUTH_E2E_RESULT": "${{ needs['operational-truth-e2e'].result }}",
    "NO_CONTROL_ROOM_NEEDED_RESULT": "${{ needs['no-control-room-needed'].result }}",
}
GATE_SCRIPT = assert_gate_shape(
    GATE,
    name="control-room-gate",
    needs=[
        "changes",
        "control-room-postgres-rls",
        "no-control-room-needed",
        "operational-truth-e2e",
    ],
    env=ENV_BINDINGS,
)
NON_SUCCESS = ("failure", "cancelled", "skipped", "neutral", "")
NON_SKIPPED = ("success", "failure", "cancelled", "neutral", "")
WRONG_JOB_MATRICES = [
    *((True, result, "success", "skipped") for result in NON_SUCCESS),
    *((True, "success", result, "skipped") for result in NON_SUCCESS),
    *((True, "success", "success", result) for result in NON_SKIPPED),
    *((False, result, "skipped", "success") for result in NON_SKIPPED),
    *((False, "skipped", result, "success") for result in NON_SKIPPED),
    *((False, "skipped", "skipped", result) for result in NON_SUCCESS),
]


def _valid_env(*, relevant: bool) -> dict[str, str]:
    return {
        "CHANGES_RESULT": "success",
        "CONTROL_ROOM": "true" if relevant else "false",
        "CONTROL_ROOM_TESTS_RESULT": "success" if relevant else "skipped",
        "OPERATIONAL_TRUTH_E2E_RESULT": "success" if relevant else "skipped",
        "NO_CONTROL_ROOM_NEEDED_RESULT": "skipped" if relevant else "success",
    }


def test_workflow_runs_once_on_stacked_prs_and_main_without_path_filters() -> None:
    events = WORKFLOW[True]
    assert events == {
        "pull_request": None,
        "push": {"branches": ["main"]},
    }
    assert WORKFLOW["permissions"] == {"contents": "read"}
    assert count_job_name("control-room-gate") == 1
    assert len(Path(__file__).read_text(encoding="utf-8").splitlines()) < 300
    assert (
        len((ROOT / "scripts/ci_control_room_paths.py").read_text().splitlines()) < 200
    )


def test_changes_and_conditional_jobs_have_exact_contract() -> None:
    changes = WORKFLOW["jobs"]["changes"]
    assert changes["outputs"] == {
        "control_room": "${{ steps.detect.outputs.control_room }}"
    }
    checkout = changes["steps"][0]
    assert checkout == {"uses": "actions/checkout@v4", "with": {"fetch-depth": 0}}

    tests = WORKFLOW["jobs"]["control-room-postgres-rls"]
    e2e = WORKFLOW["jobs"]["operational-truth-e2e"]
    noop = WORKFLOW["jobs"]["no-control-room-needed"]
    assert tests["needs"] == "changes"
    assert tests["if"] == "needs.changes.outputs.control_room == 'true'"
    assert e2e["needs"] == "changes"
    assert e2e["if"] == "needs.changes.outputs.control_room == 'true'"
    assert noop["needs"] == "changes"
    assert noop["if"] == "needs.changes.outputs.control_room == 'false'"


@pytest.mark.parametrize("relevant", [True, False])
def test_gate_accepts_only_the_two_valid_matrices(
    relevant: bool, tmp_path: Path
) -> None:
    result = run_gate(GATE_SCRIPT, _valid_env(relevant=relevant), tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("result", ["failure", "cancelled", "skipped", "neutral", ""])
def test_gate_rejects_bad_or_missing_detector_result(
    result: str, tmp_path: Path
) -> None:
    env = _valid_env(relevant=True)
    if result:
        env["CHANGES_RESULT"] = result
    else:
        env.pop("CHANGES_RESULT")
    assert_gate_rejects(run_gate(GATE_SCRIPT, env, tmp_path))


@pytest.mark.parametrize("value", ["", "TRUE", "yes", "0", "1", "False"])
def test_gate_rejects_noncanonical_boolean(value: str, tmp_path: Path) -> None:
    env = _valid_env(relevant=True)
    if value:
        env["CONTROL_ROOM"] = value
    else:
        env.pop("CONTROL_ROOM")
    assert_gate_rejects(run_gate(GATE_SCRIPT, env, tmp_path))


@pytest.mark.parametrize(
    ("relevant", "tests_result", "e2e_result", "noop_result"),
    WRONG_JOB_MATRICES,
)
def test_gate_rejects_wrong_job_matrix(
    relevant: bool,
    tests_result: str,
    e2e_result: str,
    noop_result: str,
    tmp_path: Path,
) -> None:
    env = _valid_env(relevant=relevant)
    env["CONTROL_ROOM_TESTS_RESULT"] = tests_result
    env["OPERATIONAL_TRUTH_E2E_RESULT"] = e2e_result
    env["NO_CONTROL_ROOM_NEEDED_RESULT"] = noop_result
    assert_gate_rejects(run_gate(GATE_SCRIPT, env, tmp_path))


def test_github_output_contains_one_exact_control_room_value(tmp_path: Path) -> None:
    output = tmp_path / "github-output"
    result = subprocess.run(
        ["python3", str(SCRIPT), "--files", "README.md"],
        cwd=ROOT,
        env={**os.environ, "GITHUB_OUTPUT": str(output)},
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    lines = output.read_text(encoding="utf-8").splitlines()
    assert result.stdout == ""
    assert lines.count("control_room=false") == 1


def test_invalid_diff_fails_without_false_output(tmp_path: Path) -> None:
    output = tmp_path / "github-output"
    result = subprocess.run(
        ["python3", str(SCRIPT), "--base", "missing-ref", "--head", "missing-ref"],
        cwd=ROOT,
        env={**os.environ, "GITHUB_OUTPUT": str(output)},
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode != 0
    assert not output.exists()
