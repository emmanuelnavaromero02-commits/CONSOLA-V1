"""Pure path policy for the protected Control Room CI surface."""

from __future__ import annotations

import re
from collections.abc import Iterable


_EXACT_PATHS = frozenset(
    {
        ".github/workflows/control-room-postgres-rls.yml",
        ".github/workflows/deploy-aws.yml",
        ".github/workflows/mcp-infra-pdf-security.yml",
        ".github/workflows/security.yml",
        "console/conftest.py",
        "console/app/dependencies.py",
        "console/app/domains/pipeline/control_room_refresh.py",
        "console/app/domains/pipeline/sync_state.py",
        "console/app/main.py",
        "console/app/services/adapter_idempotency.py",
        "console/app/services/audit_service.py",
        "console/app/services/banxico_readiness.py",
        "console/app/services/db_scope.py",
        "console/app/services/inegi_readiness.py",
        "console/app/services/intelligence/control_room_observation.py",
        "console/app/services/intelligence/decision_orchestrator.py",
        "console/app/services/intelligence/evidence_refs.py",
        "console/app/services/intelligence/gold_fetcher.py",
        "console/app/services/intelligence/persistence.py",
        "console/app/services/intelligence/readiness.py",
        "console/app/services/intelligence/gold_control_room.py",
        "console/app/services/permissions.py",
        "console/app/services/sec_edgar_readiness.py",
        "console/app/services/security_context.py",
        "console/app/services/control_room_service.py",
        "console/app/services/sync_control_room.py",
        "console/requirements.txt",
        "console/tests/conftest.py",
        "console/tests/test_audit_service_transactional.py",
        "console/tests/test_gold_fetcher.py",
        "console/tests/test_intelligence_control_room_canonical_persistence.py",
        "console/tests/test_intelligence_evidence_refs_attestation.py",
        "console/tests/test_ops_summary_and_version.py",
        "console/tests/test_pipeline_extract.py",
        "console/tests/test_scoped_surface_hardening.py",
        "infra/.env.example",
        "infra/bootstrap-keys.sh",
        "infra/bootstrap.sh",
        "infra/docker-compose.yml",
        "infra/terraform/infra/secretsmanager.tf",
        "pytest.ini",
        "scripts/aws-entrypoint.sh",
        "scripts/aws-env-pair.sh",
        "scripts/ci_changed_areas.py",
        "scripts/ci_control_room_paths.py",
        "scripts/validate-evidence-keyring.py",
        "tests/ci_gate_contract_helpers.py",
        "tests/conftest.py",
        "tests/decision_orchestrator_harness.py",
        "tests/requirements.txt",
        "tests/test_agentops_scheduled_monitor_contract.py",
        "tests/test_aws_beta_operations.py",
        "tests/test_aws_bootstrap_shared_env_boundary.py",
        "tests/test_aws_env_pair_transaction.py",
        "tests/test_aws_ghcr_private_auth.py",
        "tests/test_aws_evidence_compose_isolation.py",
        "tests/test_aws_evidence_env_isolation.py",
        "tests/test_aws_evidence_update_env.py",
        "tests/test_aws_secrets_manager_config.py",
        "tests/test_aws_ssm_deploy_workflow.py",
        "tests/test_ci_changed_areas.py",
        "tests/test_ci_detector_self_protection.py",
        "tests/test_control_room_ci_contract.py",
        "tests/test_control_room_evidence_keyring_runtime.py",
        "tests/test_control_room_evidence_signing_wiring.py",
        "tests/test_control_room_gate_contract.py",
        "tests/test_control_room_path_policy.py",
        "tests/test_intelligence_engine_contract.py",
        "tests/test_mcp_infra_pdf_ci_contract.py",
        "tests/test_operational_rls_console_refinement.py",
        "tests/test_operational_rls_policy_guard.py",
        "tests/test_pipeline_control_room_refresh.py",
        "tests/test_v1_router_mount.py",
    }
)

_PREFIXES = (
    "console/app/",
    "console/app/domains/decisions/",
    "console/app/services/adapters/",
    "console/app/services/control_room/",
    "console-next/src/components/control-room/",
    "console-next/src/lib/control-room/",
    "infra/init/",
    "infra/terraform-gcp/",
    "infra/terraform/deploy/",
    "mcp-infra/app/tools/control_room",
)

_INNOCUOUS_EXACT_PATHS = frozenset({"README.md"})
_INNOCUOUS_PREFIXES = ("docs/",)

_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r"^console/app/routers/control_room[^/]*\.py$",
        r"^console/app/schemas/control_room[^/]*\.py$",
        r"^console/tests/control_room_[^/]*\.py$",
        r"^console/tests/test_control_room[^/]*\.py$",
        r"^console/tests/test_decision[^/]*\.py$",
        r"^tests/test_control_room[^/]*\.py$",
        r"^tests/test_decision[^/]*\.py$",
        r"^tests-e2e/specs/[^/]*control-room[^/]*$",
        r"^scripts/aws_control_room_[^/]*\.py$",
    )
)


def _is_control_room_app_path(path: str) -> bool:
    prefix = "console-next/src/app/"
    if not path.startswith(prefix):
        return False
    relative = path.removeprefix(prefix)
    return relative.startswith("control-room/") or "/control-room/" in relative


def _is_protected_path(path: str) -> bool:
    return (
        path in _EXACT_PATHS
        or path.startswith(_PREFIXES)
        or _is_control_room_app_path(path)
        or any(pattern.fullmatch(path) for pattern in _PATTERNS)
    )


def _is_explicitly_innocuous(path: str) -> bool:
    if any(ord(char) < 32 or ord(char) == 127 for char in path):
        return False
    return path in _INNOCUOUS_EXACT_PATHS or path.startswith(_INNOCUOUS_PREFIXES)


def control_room_changed(files: Iterable[str]) -> bool:
    """Return whether any exact changed path affects protected Control Room CI."""

    paths = tuple(files)
    if not paths:
        return True
    for path in paths:
        if _is_protected_path(path) or not _is_explicitly_innocuous(path):
            return True
    return False
