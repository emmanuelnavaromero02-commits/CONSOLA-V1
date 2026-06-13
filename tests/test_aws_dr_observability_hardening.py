from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_deploy_main_aws_is_artifact_based_and_secret_safe():
    src = _read("scripts/deploy_main_aws.py")
    assert "git archive" in src
    assert "artifact_sha256" in src
    assert "S3_BUCKET_NAME" in src
    assert "host worktree preserved" in src
    assert "preserve = {{" in src
    assert "DEPLOY_REF must be a full 40-character commit SHA" in src
    assert "origin/main" in src
    assert "printenv" not in src
    assert "PRIVATE_KEY" not in src
    assert "PAT" not in src


def test_aws_ssm_large_script_upload_is_idempotent():
    src = _read("scripts/aws_ssm.py")
    assert 'OMEGA_SSM_MAX_INLINE_SCRIPT_BYTES", "12000"' in src
    assert "attempts: int = 1" in src
    assert "send_attempts: int = 1" in src
    assert "attempts=3" in src
    assert ".part-{chunk_name}" in src
    assert "cat {remote_b64}.part-* > {remote_b64}" in src


def test_aws_full_regression_orchestrates_critical_gates():
    src = _read("scripts/aws_full_regression.py")
    for token in (
        "beta_smoke_aws.py",
        "tenant_ab_e2e.py",
        "run_decision_backtest.py",
        "aws_observability_report.py",
        "aws_superset_probe.py",
        "aws_tls_status.py",
        "control-room-cycle-proof",
    ):
        assert token in src
    assert "summary.json" in src
    assert "critical" in src


def test_backup_manifest_is_verifiable_and_redacts_config_values():
    backup = _read("infra/terraform/deploy/backup.sh")
    wrapper = _read("scripts/aws_backup.py")
    for token in (
        "POSTGRES_SHA",
        "POSTGRES_GOLD_SHA",
        "lakehouse_objects.tsv",
        "config_manifest.json",
        "secrets_redacted",
        "restore_hint",
        "generated_at",
        "OMEGA_BACKUP_MANIFEST",
    ):
        assert token in backup
    assert "manifest_verifiable" in wrapper
    assert "ssm_command_id" in wrapper
    assert "printenv" not in wrapper


def test_dr_rehearsal_is_safe_by_default_and_uses_temp_restore_targets():
    src = _read("scripts/aws_dr_rehearsal.py")
    assert "isolated_temp_containers" in src
    assert "docker run -d --name" in src
    assert 'destructive": False' in src
    assert "CONFIRM_RESTORE=modecissions" not in src
    assert "restore.sh" not in src


def test_rollback_aws_defaults_to_dry_run_until_confirmed():
    src = _read("scripts/aws_rollback.py")
    assert "CONFIRM_ROLLBACK" in src
    assert "rollback dry-run" in src
    assert "bash rollback.sh" in src
    assert "args.confirm" in src


def test_observability_tls_and_superset_reports_are_honest_and_redacted():
    obs = _read("scripts/aws_observability_report.py")
    tls = _read("scripts/aws_tls_status.py")
    superset = _read("scripts/aws_superset_probe.py")
    assert "recent console errors" in obs
    assert "failed backtest runs 24h" in obs
    assert "external writeback disabled" in obs
    assert "redact(" in obs
    assert "HTTPS listener" in tls
    assert "BLOCKED" in tls
    assert "Tenant context propagation" in superset
    assert "BLOCKED" in superset
    for src in (obs, tls, superset):
        assert "printenv" not in src


def test_makefile_exposes_aws_hardening_targets():
    makefile = _read("Makefile")
    for target in (
        "deploy-main-aws:",
        "aws-full-regression:",
        "aws-observability-report:",
        "aws-tls-status:",
        "aws-superset-probe:",
        "rollback-rehearsal-aws:",
    ):
        assert target in makefile
