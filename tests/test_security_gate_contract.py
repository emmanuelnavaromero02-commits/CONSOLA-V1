from __future__ import annotations

from pathlib import Path

import pytest

from tests.ci_gate_contract_helpers import (
    assert_gate_rejects,
    assert_gate_shape,
    detector_outputs,
    load_job,
    run_gate,
)


ENV_BINDINGS = {
    "CHANGES_RESULT": "${{ needs.changes.result }}",
    "PYTHON_RUNTIME": "${{ needs.changes.outputs.python_runtime }}",
    "PYTHON_DEPS": "${{ needs.changes.outputs.python_deps }}",
    "NODE_DEPS": "${{ needs.changes.outputs.node_deps }}",
    "INFRA": "${{ needs.changes.outputs.infra }}",
    "BANDIT_RESULT": "${{ needs.bandit.result }}",
    "PIP_AUDIT_RESULT": "${{ needs['pip-audit'].result }}",
    "NPM_AUDIT_RESULT": "${{ needs['npm-audit'].result }}",
    "NO_SECURITY_SCAN_NEEDED_RESULT": (
        "${{ needs['no-security-scan-needed'].result }}"
    ),
}
JOB = load_job("security.yml", "security-gate")
SCRIPT = assert_gate_shape(
    JOB,
    name="security-gate",
    needs=["changes", "bandit", "pip-audit", "npm-audit", "no-security-scan-needed"],
    env=ENV_BINDINGS,
)
FLAGS = ("PYTHON_RUNTIME", "PYTHON_DEPS", "NODE_DEPS", "INFRA")


def _env(
    *,
    runtime: str,
    python_deps: str,
    node_deps: str,
    infra: str,
    bandit: str,
    pip_audit: str,
    npm_audit: str,
    no_security: str,
) -> dict[str, str]:
    return {
        "CHANGES_RESULT": "success",
        "PYTHON_RUNTIME": runtime,
        "PYTHON_DEPS": python_deps,
        "NODE_DEPS": node_deps,
        "INFRA": infra,
        "BANDIT_RESULT": bandit,
        "PIP_AUDIT_RESULT": pip_audit,
        "NPM_AUDIT_RESULT": npm_audit,
        "NO_SECURITY_SCAN_NEEDED_RESULT": no_security,
    }


NOOP = _env(
    runtime="false",
    python_deps="false",
    node_deps="false",
    infra="false",
    bandit="skipped",
    pip_audit="skipped",
    npm_audit="skipped",
    no_security="success",
)
BANDIT_ONLY = _env(
    runtime="true",
    python_deps="false",
    node_deps="false",
    infra="false",
    bandit="success",
    pip_audit="skipped",
    npm_audit="skipped",
    no_security="skipped",
)
PIP_ONLY = _env(
    runtime="false",
    python_deps="true",
    node_deps="false",
    infra="false",
    bandit="skipped",
    pip_audit="success",
    npm_audit="skipped",
    no_security="skipped",
)
NPM_ONLY = _env(
    runtime="false",
    python_deps="false",
    node_deps="true",
    infra="false",
    bandit="skipped",
    pip_audit="skipped",
    npm_audit="success",
    no_security="skipped",
)
RUNTIME_AND_PIP = _env(
    runtime="true",
    python_deps="true",
    node_deps="false",
    infra="false",
    bandit="success",
    pip_audit="success",
    npm_audit="skipped",
    no_security="skipped",
)
RUNTIME_AND_NPM = _env(
    runtime="true",
    python_deps="false",
    node_deps="true",
    infra="false",
    bandit="success",
    pip_audit="skipped",
    npm_audit="success",
    no_security="skipped",
)
PIP_AND_NPM = _env(
    runtime="false",
    python_deps="true",
    node_deps="true",
    infra="false",
    bandit="skipped",
    pip_audit="success",
    npm_audit="success",
    no_security="skipped",
)
ALL_DEPS = _env(
    runtime="true",
    python_deps="true",
    node_deps="true",
    infra="false",
    bandit="success",
    pip_audit="success",
    npm_audit="success",
    no_security="skipped",
)
INFRA_ALL = _env(
    runtime="false",
    python_deps="false",
    node_deps="false",
    infra="true",
    bandit="success",
    pip_audit="success",
    npm_audit="success",
    no_security="skipped",
)


def test_security_gate_has_exact_fail_closed_contract() -> None:
    assert load_job("security.yml", "no-security-scan-needed")["if"].splitlines() == [
        "needs.changes.outputs.python_runtime == 'false' &&",
        "needs.changes.outputs.python_deps == 'false' &&",
        "needs.changes.outputs.node_deps == 'false' &&",
        "needs.changes.outputs.infra == 'false'",
    ]


@pytest.mark.parametrize(
    "env",
    [
        NOOP,
        BANDIT_ONLY,
        PIP_ONLY,
        NPM_ONLY,
        RUNTIME_AND_PIP,
        RUNTIME_AND_NPM,
        PIP_AND_NPM,
        ALL_DEPS,
        INFRA_ALL,
    ],
    ids=[
        "no-op",
        "bandit",
        "pip-audit",
        "npm-audit",
        "bandit-pip",
        "bandit-npm",
        "pip-npm",
        "all-deps",
        "infra-all",
    ],
)
def test_security_gate_accepts_valid_detector_combinations(
    env: dict[str, str],
    tmp_path: Path,
) -> None:
    result = run_gate(SCRIPT, env, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("result", ["failure", "cancelled", "skipped", "neutral", ""])
def test_security_gate_rejects_unsuccessful_or_missing_detector(
    result: str,
    tmp_path: Path,
) -> None:
    env = NOOP.copy()
    if result:
        env["CHANGES_RESULT"] = result
    else:
        env.pop("CHANGES_RESULT")
    assert_gate_rejects(run_gate(SCRIPT, env, tmp_path))


@pytest.mark.parametrize("name", FLAGS)
@pytest.mark.parametrize("value", ["", "yes", "TRUE"])
def test_security_gate_rejects_empty_or_invalid_outputs(
    name: str,
    value: str,
    tmp_path: Path,
) -> None:
    env = NOOP.copy()
    env[name] = value
    assert_gate_rejects(run_gate(SCRIPT, env, tmp_path))


@pytest.mark.parametrize("name", FLAGS)
def test_security_gate_rejects_missing_outputs(name: str, tmp_path: Path) -> None:
    env = NOOP.copy()
    env.pop(name)
    assert_gate_rejects(run_gate(SCRIPT, env, tmp_path))


@pytest.mark.parametrize(
    ("env", "job"),
    [
        (BANDIT_ONLY, "BANDIT_RESULT"),
        (PIP_ONLY, "PIP_AUDIT_RESULT"),
        (NPM_ONLY, "NPM_AUDIT_RESULT"),
        (NOOP, "NO_SECURITY_SCAN_NEEDED_RESULT"),
    ],
    ids=["bandit", "pip-audit", "npm-audit", "no-op"],
)
@pytest.mark.parametrize(
    "bad_result", ["failure", "cancelled", "skipped", "neutral", ""]
)
def test_security_gate_rejects_bad_expected_job_results(
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
    "job",
    ["BANDIT_RESULT", "PIP_AUDIT_RESULT", "NPM_AUDIT_RESULT"],
)
def test_security_gate_rejects_non_applicable_scanner_that_ran(
    job: str,
    tmp_path: Path,
) -> None:
    env = NOOP.copy()
    env[job] = "success"
    assert_gate_rejects(run_gate(SCRIPT, env, tmp_path))


def test_security_gate_rejects_noop_job_when_scanner_applies(tmp_path: Path) -> None:
    env = BANDIT_ONLY.copy()
    env["NO_SECURITY_SCAN_NEEDED_RESULT"] = "success"
    assert_gate_rejects(run_gate(SCRIPT, env, tmp_path))


@pytest.mark.parametrize(
    ("changed_file", "expected"),
    [
        (
            "README.md",
            {
                "python_runtime": "false",
                "python_deps": "false",
                "node_deps": "false",
                "infra": "false",
            },
        ),
        ("console/app/main.py", {"python_runtime": "true"}),
        ("mcp-infra/requirements.txt", {"python_deps": "true"}),
        ("console-next/package-lock.json", {"node_deps": "true"}),
        (".github/workflows/security.yml", {"infra": "true"}),
    ],
)
def test_security_gate_uses_real_detector_outputs(
    changed_file: str,
    expected: dict[str, str],
) -> None:
    outputs = detector_outputs(changed_file)
    for name in ("python_runtime", "python_deps", "node_deps", "infra"):
        assert outputs[name] == expected.get(name, "false")
