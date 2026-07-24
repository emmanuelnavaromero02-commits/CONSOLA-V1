from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from tests.ci_gate_contract_helpers import (
    assert_gate_rejects,
    assert_gate_shape,
    count_job_name,
    detector_outputs,
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
    "NO_CONTROL_ROOM_NEEDED_RESULT": "${{ needs['no-control-room-needed'].result }}",
}
GATE_SCRIPT = assert_gate_shape(
    GATE,
    name="control-room-gate",
    needs=["changes", "control-room-postgres-rls", "no-control-room-needed"],
    env=ENV_BINDINGS,
)
NON_SUCCESS = ("failure", "cancelled", "skipped", "neutral", "")
NON_SKIPPED = ("success", "failure", "cancelled", "neutral", "")
WRONG_JOB_MATRICES = [
    *((True, result, "skipped") for result in NON_SUCCESS),
    *((True, "success", result) for result in NON_SKIPPED),
    *((False, result, "success") for result in NON_SKIPPED),
    *((False, "skipped", result) for result in NON_SUCCESS),
]

LEGACY_AND_NEW_PATHS = """
console/app/main.py console/conftest.py console/app/routers/control_room.py console/app/routers/control_room_admin.py console/app/schemas/control_room_item.py
console/app/dependencies.py console/app/domains/decisions/service.py console/app/domains/pipeline/control_room_refresh.py
console/app/domains/pipeline/sync_state.py console/app/services/permissions.py console/app/services/audit_service.py console/app/services/security_context.py
console/app/services/adapter_idempotency.py console/app/services/adapters/base.py
console/app/services/db_scope.py console/app/services/banxico_readiness.py console/app/services/inegi_readiness.py
console/app/services/sec_edgar_readiness.py
console/app/services/intelligence/decision_orchestrator.py console/app/services/intelligence/control_room_observation.py
console/app/services/intelligence/evidence_refs.py console/app/services/intelligence/gold_fetcher.py
console/app/services/intelligence/persistence.py console/app/services/intelligence/readiness.py console/app/services/intelligence/gold_control_room.py
console/app/services/control_room/core.py console/app/services/control_room_service.py console/app/services/sync_control_room.py
console/requirements.txt tests/requirements.txt pytest.ini
console/tests/control_room_helpers.py console/tests/conftest.py
console/tests/test_audit_service_transactional.py console/tests/test_control_room_dashboard.py console/tests/test_decision_service.py
console/tests/test_intelligence_control_room_canonical_persistence.py
console/tests/test_intelligence_evidence_refs_attestation.py console/tests/test_gold_fetcher.py console/tests/test_ops_summary_and_version.py
console/tests/test_pipeline_extract.py console/tests/test_scoped_surface_hardening.py
tests/decision_orchestrator_harness.py tests/test_control_room_dashboard.py
tests/test_control_room_live_postgres_transition.py tests/test_decision_orchestrator.py
tests/test_pipeline_control_room_refresh.py
tests/test_agentops_scheduled_monitor_contract.py tests/test_aws_beta_operations.py
tests/test_aws_bootstrap_shared_env_boundary.py
tests/test_aws_evidence_compose_isolation.py tests/test_aws_env_pair_transaction.py
tests/test_aws_ssm_deploy_workflow.py tests/test_aws_evidence_env_isolation.py
tests/test_aws_evidence_update_env.py tests/test_aws_secrets_manager_config.py tests/test_control_room_ci_contract.py
tests/test_control_room_gate_contract.py
tests/test_control_room_evidence_keyring_runtime.py
tests/test_control_room_evidence_signing_wiring.py tests/test_ci_changed_areas.py
tests/test_ci_detector_self_protection.py tests/ci_gate_contract_helpers.py
tests/test_intelligence_engine_contract.py tests/test_mcp_infra_pdf_ci_contract.py tests/test_v1_router_mount.py
tests/test_operational_rls_console_refinement.py
tests/test_operational_rls_policy_guard.py tests/conftest.py infra/.env.example
infra/bootstrap-keys.sh infra/bootstrap.sh infra/docker-compose.yml infra/init/01-schema.sql
infra/terraform-gcp/main.tf
infra/terraform/deploy/docker-compose.aws.yml
infra/terraform/infra/secretsmanager.tf scripts/aws-env-pair.sh
scripts/aws-entrypoint.sh scripts/validate-evidence-keyring.py
scripts/ci_changed_areas.py scripts/ci_control_room_paths.py scripts/aws_control_room_gold_engine_probe.py
scripts/aws_control_room_mock_volume_probe.py
.github/workflows/deploy-aws.yml
.github/workflows/control-room-postgres-rls.yml
.github/workflows/mcp-infra-pdf-security.yml .github/workflows/security.yml console-next/src/app/control-room/page.tsx
console-next/src/app/admin/control-room/page.tsx console-next/src/components/control-room/Status.tsx
console-next/src/lib/control-room/client.ts
tests-e2e/specs/admin-control-room.spec.ts
""".split()


def _valid_env(*, relevant: bool) -> dict[str, str]:
    return {
        "CHANGES_RESULT": "success",
        "CONTROL_ROOM": "true" if relevant else "false",
        "CONTROL_ROOM_TESTS_RESULT": "success" if relevant else "skipped",
        "NO_CONTROL_ROOM_NEEDED_RESULT": "skipped" if relevant else "success",
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


def _detector_diff(repo: Path, base: str, head: str) -> dict[str, str]:
    result = subprocess.run(
        ["python3", str(SCRIPT), "--base", base, "--head", head],
        cwd=repo,
        env={key: value for key, value in os.environ.items() if key != "GITHUB_OUTPUT"},
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    return dict(line.split("=", 1) for line in result.stdout.splitlines())


def test_workflow_runs_once_on_main_without_path_filters() -> None:
    events = WORKFLOW[True]
    assert events == {
        "pull_request": {"branches": ["main"]},
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
    noop = WORKFLOW["jobs"]["no-control-room-needed"]
    assert tests["needs"] == "changes"
    assert tests["if"] == "needs.changes.outputs.control_room == 'true'"
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
    ("relevant", "tests_result", "noop_result"),
    WRONG_JOB_MATRICES,
)
def test_gate_rejects_wrong_job_matrix(
    relevant: bool, tests_result: str, noop_result: str, tmp_path: Path
) -> None:
    env = _valid_env(relevant=relevant)
    env["CONTROL_ROOM_TESTS_RESULT"] = tests_result
    env["NO_CONTROL_ROOM_NEEDED_RESULT"] = noop_result
    assert_gate_rejects(run_gate(GATE_SCRIPT, env, tmp_path))


@pytest.mark.parametrize("path", LEGACY_AND_NEW_PATHS)
def test_all_legacy_and_new_paths_trigger_heavy_suite(path: str) -> None:
    assert detector_outputs(path)["control_room"] == "true"


@pytest.mark.parametrize(
    "path",
    [
        "README.md",
        "docs/control-room-operations.md",
        "console-next/package.json",
        "console-next/package-lock.json",
        "console-next/src/app/page.tsx",
        "console-next/src/components/Button.tsx",
        "tests-e2e/specs/unrelated.spec.ts",
    ],
)
def test_unrelated_paths_use_noop(path: str) -> None:
    assert detector_outputs(path)["control_room"] == "false"


def test_mixed_change_is_relevant() -> None:
    outputs = detector_outputs("README.md", "console/app/services/control_room/core.py")
    assert outputs["control_room"] == "true"


def test_rename_preserves_old_and_new_paths(tmp_path: Path) -> None:
    repo = tmp_path / "rename"
    _init_repo(repo)
    old = repo / "console/app/main.py"
    old.parent.mkdir(parents=True)
    old.write_text("old\n", encoding="utf-8")
    base = _commit(repo, "base")
    _git(repo, "mv", "console/app/main.py", "unprotected.py")
    head = _commit(repo, "rename")

    outputs = _detector_diff(repo, base, head)
    assert json.loads(outputs["changed_files"]) == [
        "console/app/main.py",
        "unprotected.py",
    ]
    assert outputs["control_room"] == "true"


def test_nul_diff_preserves_special_filenames(tmp_path: Path) -> None:
    repo = tmp_path / "special"
    _init_repo(repo)
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    base = _commit(repo, "base")
    names = [
        "console/app/services/control_room/with space.py",
        "console/app/services/control_room/with\ttab.py",
        "console/app/services/control_room/with\nnewline.py",
    ]
    for name in names:
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("changed\n", encoding="utf-8")
    head = _commit(repo, "special paths")

    outputs = _detector_diff(repo, base, head)
    assert sorted(json.loads(outputs["changed_files"])) == sorted(names)
    assert outputs["control_room"] == "true"


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
