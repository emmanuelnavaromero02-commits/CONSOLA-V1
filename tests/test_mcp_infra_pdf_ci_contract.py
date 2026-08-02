from pathlib import Path

import pytest

from tests.ci_gate_contract_helpers import (
    assert_gate_rejects,
    assert_gate_shape,
    load_job,
    load_workflow,
    run_gate,
)

ENV_BINDINGS = {
    "FUNCTIONAL_PDF_RESULT": "${{ needs['functional-pdf'].result }}",
}
JOB = load_job("mcp-infra-pdf-security.yml", "pdf-gate")
SCRIPT = assert_gate_shape(
    JOB,
    name="pdf-gate",
    needs=["functional-pdf"],
    env=ENV_BINDINGS,
)


def test_pdf_security_workflow_runs_real_functional_tests_on_every_pr() -> None:
    workflow = load_workflow("mcp-infra-pdf-security.yml")
    events = workflow[True]
    assert events["pull_request"] == {"branches": ["main"]}
    assert "paths" not in events["pull_request"]
    assert events["push"] == {"branches": ["main"]}
    assert workflow["permissions"] == {"contents": "read"}

    steps = workflow["jobs"]["functional-pdf"]["steps"]
    command = next(
        step["run"]
        for step in steps
        if step.get("name") == "Run protected PDF ingestion regressions"
    )
    for test_file in (
        "tests/test_mcp_infra_pdf_ingest.py",
        "tests/test_mcp_infra_pdf_capacity.py",
        "tests/test_mcp_infra_pdf_compose_capacity.py",
        "tests/test_pypdf_security.py",
        "tests/test_mcp_infra_pdf_ci_contract.py",
        "tests/test_lint_gate_contract.py",
        "tests/test_security_gate_contract.py",
        "tests/test_ci_detector_self_protection.py",
        "tests/test_control_room_ci_contract.py",
        "tests/test_control_room_gate_contract.py",
        "tests/test_control_room_path_policy.py",
    ):
        assert test_file in command


def test_pdf_functional_report_has_exact_floor_and_clean_junit_contract() -> None:
    workflow = load_workflow("mcp-infra-pdf-security.yml")
    steps = workflow["jobs"]["functional-pdf"]["steps"]
    run = next(
        step["run"]
        for step in steps
        if step.get("name") == "Run protected PDF ingestion regressions"
    )
    verify = next(
        step["run"]
        for step in steps
        if step.get("name") == "Verify protected PDF report"
    )
    assert "--junitxml=/tmp/functional-pdf.xml" in run
    assert 'minimum=332, label="Functional PDF"' in " ".join(verify.split())
    assert '("tests", "skipped", "failures", "errors")' in verify
    assert 'for name in ("skipped", "failures", "errors")' in verify


def test_pdf_gate_has_exact_fail_closed_contract() -> None:
    assert JOB["name"] == "pdf-gate"


def test_pdf_gate_accepts_success(tmp_path: Path) -> None:
    result = run_gate(SCRIPT, {"FUNCTIONAL_PDF_RESULT": "success"}, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    "result",
    ["failure", "cancelled", "skipped", "neutral", ""],
)
def test_pdf_gate_rejects_every_non_success_result(result: str, tmp_path: Path) -> None:
    env = {"FUNCTIONAL_PDF_RESULT": result} if result else {}
    assert_gate_rejects(run_gate(SCRIPT, env, tmp_path))
