from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from scripts.ci_control_room_paths import control_room_changed
from tests.ci_gate_contract_helpers import detector_outputs


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/ci_changed_areas.py"
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
tests/test_control_room_gate_contract.py tests/test_control_room_path_policy.py
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
RUNTIME_DEPENDENCIES = (
    "console/app/security.py",
    "console/app/services/auth.py",
    "console/app/services/csrf.py",
    "console/app/services/intelligence/history.py",
    "console/app/static/console-next/control-room/chunks/app.js",
    "mcp-infra/app/tools/control_room.py",
    "console/app/future_runtime_dependency.py",
)


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


@pytest.mark.parametrize("path", LEGACY_AND_NEW_PATHS)
def test_all_legacy_and_new_paths_trigger_heavy_suite(path: str) -> None:
    assert detector_outputs(path)["control_room"] == "true"


@pytest.mark.parametrize("path", RUNTIME_DEPENDENCIES)
def test_runtime_dependencies_and_unknown_console_app_paths_are_relevant(
    path: str,
) -> None:
    assert control_room_changed([path])
    assert detector_outputs(path)["control_room"] == "true"


@pytest.mark.parametrize(
    "paths",
    [
        ["README.md"],
        ["docs/control-room-operations.md"],
        ["README.md", "docs/architecture/control-room.md"],
    ],
)
def test_only_explicit_documentation_uses_noop(paths: list[str]) -> None:
    assert not control_room_changed(paths)
    assert detector_outputs(*paths)["control_room"] == "false"


@pytest.mark.parametrize(
    "path",
    [
        "docs/control\nroom.md",
        "docs/control\troom.md",
        "docs/control\0room.md",
    ],
)
def test_documentation_with_control_characters_fails_closed(path: str) -> None:
    assert control_room_changed([path])


@pytest.mark.parametrize(
    "path",
    [
        "cartridges/future_runtime.py",
        "console-next/src/components/Button.tsx",
        "mcp-infra/app/future_runtime.py",
    ],
)
def test_unknown_non_documentation_paths_fail_closed(path: str) -> None:
    assert detector_outputs(path)["control_room"] == "true"


def test_empty_or_mixed_change_fails_closed() -> None:
    assert control_room_changed([])
    outputs = detector_outputs("README.md", "console/app/services/auth.py")
    assert outputs["control_room"] == "true"


@pytest.mark.parametrize(
    ("old_name", "new_name"),
    [
        ("console/app/security.py", "docs/security.md"),
        ("docs/security.md", "console/app/security.py"),
    ],
)
def test_rename_to_or_from_protected_surface_is_relevant(
    tmp_path: Path, old_name: str, new_name: str
) -> None:
    repo = tmp_path / "rename"
    _init_repo(repo)
    old = repo / old_name
    old.parent.mkdir(parents=True)
    old.write_text("old\n", encoding="utf-8")
    base = _commit(repo, "base")
    new = repo / new_name
    new.parent.mkdir(parents=True, exist_ok=True)
    _git(repo, "mv", old_name, new_name)
    head = _commit(repo, "rename")

    outputs = _detector_diff(repo, base, head)
    assert set(json.loads(outputs["changed_files"])) == {old_name, new_name}
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


def test_module_and_test_remain_modular() -> None:
    assert (
        len((ROOT / "scripts/ci_control_room_paths.py").read_text().splitlines()) < 200
    )
    assert len(Path(__file__).read_text(encoding="utf-8").splitlines()) <= 300
