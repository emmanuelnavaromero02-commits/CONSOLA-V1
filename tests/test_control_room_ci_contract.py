from pathlib import Path

from scripts.ci_control_room_paths import control_room_changed


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/control-room-postgres-rls.yml"
FOCAL_MINIMUM = 7446
POSTGRES_MINIMUM = 87
TENANT_EXECUTE_ISOLATION = ROOT / (
    "tests/test_control_room_live_postgres_tenant_execute_isolation.py"
)

P11_RELEVANT_PATHS = (
    "console/app/main.py",
    "console/app/routers/control_room.py",
    "console/app/routers/control_room_surfaces.py",
    "console/app/schemas/__init__.py",
    "console/app/schemas/control_room_diagnostic_enums.py",
    "console/app/schemas/control_room_surfaces.py",
    "console/app/dependencies.py",
    "console/app/domains/decisions/**",
    "console/app/domains/pipeline/control_room_refresh.py",
    "console/app/domains/pipeline/sync_state.py",
    "console/app/services/permissions.py",
    "console/app/services/audit_service.py",
    "console/app/services/security_context.py",
    "console/app/services/adapter_idempotency.py",
    "console/app/services/adapters/**",
    "console/app/services/db_scope.py",
    "console/app/services/banxico_readiness.py",
    "console/app/services/inegi_readiness.py",
    "console/app/services/sec_edgar_readiness.py",
    "console/app/services/intelligence/decision_orchestrator.py",
    "console/app/services/intelligence/control_room_observation.py",
    "console/app/services/intelligence/evidence_refs.py",
    "console/app/services/intelligence/gold_fetcher.py",
    "console/app/services/intelligence/market_decision_validation.py",
    "console/app/services/intelligence/persistence.py",
    "console/app/services/intelligence/readiness.py",
    "console/app/services/control_room/**",
    "mcp-infra/app/tools/control_room.py",
    "console/tests/control_room_*.py",
    "console/tests/conftest.py",
    "console/tests/test_audit_service_transactional.py",
    "console/tests/test_control_room*.py",
    "console/tests/test_gold_fetcher.py",
    "console/tests/test_ops_summary_and_version.py",
    "console/tests/test_intelligence_control_room_canonical_persistence.py",
    "console/tests/test_intelligence_evidence_refs_attestation.py",
    "console/tests/test_scoped_surface_hardening.py",
    "tests/test_control_room*.py",
    "tests/test_control_room_evidence_keyring_runtime.py",
    "tests/test_control_room_evidence_signing_wiring.py",
    "tests/decision_orchestrator_harness.py",
    "tests/test_aws_beta_operations.py",
    "tests/test_aws_bootstrap_shared_env_boundary.py",
    "tests/test_aws_evidence_compose_isolation.py",
    "tests/test_aws_evidence_update_env.py",
    "tests/test_aws_secrets_manager_config.py",
    "tests/test_intelligence_engine_contract.py",
    "tests/test_mcp_control_room_read_permissions.py",
    "tests/test_operational_rls_console_refinement.py",
    "tests/test_operational_rls_policy_guard.py",
    "tests/test_control_room_live_postgres*.py",
    "tests/test_aws_ssm_deploy_workflow.py",
    "tests/conftest.py",
    "infra/.env.example",
    "infra/bootstrap-keys.sh",
    "infra/bootstrap.sh",
    "infra/docker-compose.yml",
    "infra/init/**",
    "infra/terraform-gcp/**",
    "infra/terraform/deploy/**",
    "infra/terraform/infra/secretsmanager.tf",
    "scripts/aws-env-pair.sh",
    "scripts/aws-entrypoint.sh",
    "scripts/validate-evidence-keyring.py",
    ".github/workflows/deploy-aws.yml",
    ".github/workflows/control-room-postgres-rls.yml",
)

REQUIRED_RELATED_TESTS = (
    "console/tests/test_audit_service_transactional.py",
    "console/tests/test_decision*.py",
    "console/tests/test_gold_fetcher.py",
    "console/tests/test_ops_summary_and_version.py",
    "console/tests/test_intelligence_control_room_canonical_persistence.py",
    "console/tests/test_intelligence_evidence_refs_attestation.py",
    "console/tests/test_pipeline_extract.py",
    "tests/test_decision*.py",
    "tests/test_pipeline_control_room_refresh.py",
    "tests/test_agentops_scheduled_monitor_contract.py",
    "tests/test_aws_beta_operations.py",
    "tests/test_aws_bootstrap_shared_env_boundary.py",
    "tests/test_aws_evidence_compose_isolation.py",
    "tests/test_aws_env_pair_transaction.py",
    "tests/test_aws_ssm_deploy_workflow.py",
    "tests/test_aws_evidence_env_isolation.py",
    "tests/test_aws_evidence_update_env.py",
    "tests/test_aws_secrets_manager_config.py",
    "tests/test_control_room_ci_contract.py",
    "tests/test_control_room_gate_contract.py",
    "tests/test_control_room_path_policy.py",
    "tests/test_control_room_evidence_keyring_runtime.py",
    "tests/test_control_room_evidence_signing_wiring.py",
    "tests/test_intelligence_engine_contract.py",
    "tests/test_market_decision_validation_e2e.py",
    "tests/test_mcp_infra_pdf_ci_contract.py",
    "tests/test_mcp_control_room_read_permissions.py",
    "tests/test_v1_router_mount.py",
)

FOCAL_TESTS = (
    "console/tests/test_control_room*.py",
    "console/tests/test_scoped_surface_hardening.py",
    *REQUIRED_RELATED_TESTS,
)

LIVE_POSTGRES_TESTS = (
    "tests/test_operational_rls_console_refinement.py",
    "tests/test_operational_rls_policy_guard.py",
    "tests/test_control_room_live_postgres*.py",
)


def _workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_p11_paths_activate_the_fail_closed_detector():
    for path in P11_RELEVANT_PATHS:
        assert control_room_changed((path,)), path


def test_control_room_workflow_runs_all_related_contract_suites():
    focal_step = (
        _workflow_text()
        .split("- name: Run focal", 1)[1]
        .split("- name: Run live", 1)[0]
    )
    for test_path in FOCAL_TESTS:
        assert test_path in focal_step


def test_control_room_workflow_runs_all_live_postgres_suites():
    live_step = (
        _workflow_text()
        .split("- name: Run live", 1)[1]
        .split("- name: Verify focal", 1)[0]
    )
    for test_path in LIVE_POSTGRES_TESTS:
        assert test_path in live_step


def test_cross_tenant_execute_regression_is_routed_to_live_postgres_gate():
    assert TENANT_EXECUTE_ISOLATION.is_file()
    assert TENANT_EXECUTE_ISOLATION.match("test_control_room_live_postgres*.py")
    assert "tests/test_control_room_live_postgres*.py" in _workflow_text()


def test_focal_junit_guard_requires_current_minimum_and_zero_bad_results():
    text = _workflow_text()
    assert "--junitxml=/tmp/control-room-focal.xml" in text
    assert (
        f'verify_junit("/tmp/control-room-focal.xml", minimum={FOCAL_MINIMUM}' in text
    )


def test_postgres_junit_guard_requires_current_minimum_and_zero_bad_results():
    text = _workflow_text()
    assert "--junitxml=/tmp/control-room-postgres-rls.xml" in text
    assert (
        f'verify_junit("/tmp/control-room-postgres-rls.xml", minimum={POSTGRES_MINIMUM}'
        in text
    )


def test_both_junit_reports_fail_closed_on_missing_or_bad_results():
    text = _workflow_text()
    verifier = text.split("python - <<'PY'", 1)[1]
    for field in ("skipped", "failures", "errors"):
        assert field in verifier
    assert "report missing" in verifier.lower()
    assert "if any(bad.values())" in verifier
