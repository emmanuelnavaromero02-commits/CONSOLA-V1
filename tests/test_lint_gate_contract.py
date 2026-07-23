from __future__ import annotations

from pathlib import Path

import pytest

from tests.ci_gate_contract_helpers import (
    assert_gate_rejects,
    assert_gate_shape,
    count_job_name,
    load_job,
    run_gate,
)


ENV_BINDINGS = {
    "CHANGES_RESULT": "${{ needs.changes.result }}",
    "PYTHON": "${{ needs.changes.outputs.python }}",
    "FRONTEND": "${{ needs.changes.outputs.frontend }}",
    "RUFF_RESULT": "${{ needs.ruff.result }}",
    "CONSOLE_NEXT_RESULT": "${{ needs['console-next'].result }}",
    "NO_LINT_NEEDED_RESULT": "${{ needs['no-lint-needed'].result }}",
}
JOB = load_job("lint.yml", "lint-gate")
SCRIPT = assert_gate_shape(
    JOB,
    name="lint-gate",
    needs=["changes", "ruff", "console-next", "no-lint-needed"],
    env=ENV_BINDINGS,
)


def _case(
    python: str,
    frontend: str,
    ruff: str,
    console_next: str,
    no_lint: str,
) -> dict[str, str]:
    return {
        "CHANGES_RESULT": "success",
        "PYTHON": python,
        "FRONTEND": frontend,
        "RUFF_RESULT": ruff,
        "CONSOLE_NEXT_RESULT": console_next,
        "NO_LINT_NEEDED_RESULT": no_lint,
    }


NOOP = _case("false", "false", "skipped", "skipped", "success")
PYTHON_ONLY = _case("true", "false", "success", "skipped", "skipped")
FRONTEND_ONLY = _case("false", "true", "skipped", "success", "skipped")
BOTH = _case("true", "true", "success", "success", "skipped")


def test_lint_gate_has_exact_fail_closed_contract() -> None:
    assert load_job("lint.yml", "no-lint-needed")["if"] == (
        "needs.changes.outputs.python == 'false' && "
        "needs.changes.outputs.frontend == 'false'"
    )


@pytest.mark.parametrize("name", ["lint-gate", "security-gate", "pdf-gate"])
def test_required_gate_context_name_is_unique(name: str) -> None:
    assert count_job_name(name) == 1


@pytest.mark.parametrize(
    "env",
    [NOOP, PYTHON_ONLY, FRONTEND_ONLY, BOTH],
    ids=["no-op", "python", "frontend", "both"],
)
def test_lint_gate_accepts_all_valid_detector_combinations(
    env: dict[str, str],
    tmp_path: Path,
) -> None:
    result = run_gate(SCRIPT, env, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("result", ["failure", "cancelled", "skipped", "neutral", ""])
def test_lint_gate_rejects_unsuccessful_or_missing_changes(
    result: str, tmp_path: Path
) -> None:
    env = NOOP.copy()
    if result:
        env["CHANGES_RESULT"] = result
    else:
        env.pop("CHANGES_RESULT")
    assert_gate_rejects(run_gate(SCRIPT, env, tmp_path))


@pytest.mark.parametrize("name", ["PYTHON", "FRONTEND"])
@pytest.mark.parametrize("value", ["", "yes", "TRUE"])
def test_lint_gate_rejects_empty_or_invalid_outputs(
    name: str, value: str, tmp_path: Path
) -> None:
    env = NOOP.copy()
    env[name] = value
    assert_gate_rejects(run_gate(SCRIPT, env, tmp_path))


@pytest.mark.parametrize("name", ["PYTHON", "FRONTEND"])
def test_lint_gate_rejects_missing_outputs(name: str, tmp_path: Path) -> None:
    env = NOOP.copy()
    env.pop(name)
    assert_gate_rejects(run_gate(SCRIPT, env, tmp_path))


@pytest.mark.parametrize(
    ("env", "job"),
    [
        (PYTHON_ONLY, "RUFF_RESULT"),
        (FRONTEND_ONLY, "CONSOLE_NEXT_RESULT"),
        (NOOP, "NO_LINT_NEEDED_RESULT"),
    ],
    ids=["ruff", "console-next", "no-lint-needed"],
)
@pytest.mark.parametrize(
    "bad_result", ["failure", "cancelled", "skipped", "neutral", ""]
)
def test_lint_gate_rejects_bad_expected_job_results(
    env: dict[str, str],
    job: str,
    bad_result: str,
    tmp_path: Path,
) -> None:
    candidate = env.copy()
    if bad_result:
        candidate[job] = bad_result
    else:
        candidate.pop(job)
    assert_gate_rejects(run_gate(SCRIPT, candidate, tmp_path))


@pytest.mark.parametrize(
    ("env", "job"),
    [
        (NOOP, "RUFF_RESULT"),
        (NOOP, "CONSOLE_NEXT_RESULT"),
        (PYTHON_ONLY, "NO_LINT_NEEDED_RESULT"),
    ],
    ids=["ruff", "console-next", "no-lint-needed"],
)
def test_lint_gate_rejects_non_applicable_jobs_that_ran(
    env: dict[str, str],
    job: str,
    tmp_path: Path,
) -> None:
    candidate = env.copy()
    candidate[job] = "success"
    assert_gate_rejects(run_gate(SCRIPT, candidate, tmp_path))
