from __future__ import annotations

import ast
from pathlib import Path

import yaml


REPO = Path(__file__).resolve().parents[1]
AWS_COMPOSE = REPO / "infra/terraform/deploy/docker-compose.aws.yml"
MAKEFILE = REPO / "Makefile"


def _read(path: str | Path) -> str:
    return (REPO / path if isinstance(path, str) else path).read_text(encoding="utf-8")


def test_aws_console_mounts_host_version_file() -> None:
    doc = yaml.safe_load(_read(AWS_COMPOSE))
    volumes = doc["services"]["console"]["volumes"]

    assert "/opt/modecissions/VERSION:/app/VERSION:ro" in volumes


def test_makefile_exposes_aws_beta_operational_targets() -> None:
    makefile = _read(MAKEFILE)
    for target, script in {
        "beta-smoke-aws:": "scripts/beta_smoke_aws.py",
        "seed-replicon-beta-gold-aws:": "scripts/seed_replicon_beta_gold_aws.py",
        "tenant-ab-local:": "scripts/tenant_ab_e2e.py --target local",
        "tenant-ab-aws:": "scripts/tenant_ab_e2e.py --target aws",
        "backup-aws:": "scripts/aws_backup.py",
        "dr-rehearsal-aws:": "scripts/aws_dr_rehearsal.py",
        "rollback-aws:": "scripts/aws_rollback.py",
    }.items():
        assert target in makefile
        assert script in makefile


def test_aws_ssm_helper_parses_and_redacts_sensitive_output() -> None:
    source = _read("scripts/aws_ssm.py")
    ast.parse(source)
    for needle in (
        "SENSITIVE_PATTERNS",
        "authorization",
        "set-cookie",
        "PASSWORD",
        "TOKEN",
        "SECRET",
        "send_ssm_script",
        "CommandId",
    ):
        assert needle in source

    import sys

    sys.path.insert(0, str(REPO / "scripts"))
    from aws_ssm import redact  # noqa: PLC0415

    sample = "Authorization: Bearer abc\nJWT_SECRET_KEY=super-secret\npostgresql://u:p@host/db"
    redacted = redact(sample)
    assert "abc" not in redacted
    assert "super-secret" not in redacted
    assert ":p@" not in redacted


def test_beta_smoke_aws_records_metadata_and_has_dual_layer_checks() -> None:
    source = _read("scripts/beta_smoke_aws.py")
    ast.parse(source)
    for needle in (
        "ssm_command_id",
        "instance_id",
        "region",
        "deploy_ref",
        "image_tag",
        "generated_at_utc",
        "public_url",
        "remote_stdout_redacted.txt",
        "remote_stderr_redacted.txt",
        "/opt/modecissions",
        "host VERSION file",
        "public-alb",
        "internal-ec2",
        "http://127.0.0.1:8000/healthz",
        "http://127.0.0.1:8000/readyz",
        "readyz?require_data=1",
        "gold_consultor_mensual",
        "gold_pnl_mensual",
        "gold_forecast_mensual",
        "Gold read role NOBYPASSRLS",
        "omega_refinement_gold",
        "golden_path",
        "OMEGA_BETA_REQUIRE_HUBSPOT",
        "HubSpot optional golden path",
        "external write-back disabled",
    ):
        assert needle in source
    assert ".env" not in source.split("remote_stdout_redacted.txt", 1)[0] or "env_value" in source
    assert "printenv" not in source or "CONTROL_ROOM_ENABLE_EXTERNAL_WRITEBACK" in source


def test_replicon_seed_has_scoped_lineage_and_checksum_idempotency() -> None:
    source = _read("scripts/seed_replicon_beta_gold.py")
    ast.parse(source)
    for needle in (
        "replicon_beta_gold_scope_fingerprint",
        "checksum",
        "hashlib.sha256",
        "--verify-idempotent-runs",
        "before_equals_after",
        "storage_prefix",
        "storage_uri LIKE",
    ):
        assert needle in source
    assert "DELETE FROM silver_lineage WHERE cartridge_id = %s AND source_batch_id = %s" not in source


def test_seed_replicon_beta_gold_aws_requires_scope_and_three_runs() -> None:
    source = _read("scripts/seed_replicon_beta_gold_aws.py")
    ast.parse(source)
    for needle in (
        "OMEGA_SEED_TENANT_ID and OMEGA_SEED_WORKSPACE_ID are required",
        "--verify-runs must be >= 3",
        "OMEGA_SEED_VERIFY_IDEMPOTENT_RUNS",
        "Replicon seed idempotent",
        "before_equals_after",
        "remote_stdout_redacted.txt",
    ):
        assert needle in source


def test_tenant_ab_harness_includes_positive_and_forbidden_probes() -> None:
    source = _read("scripts/tenant_ab_e2e.py")
    ast.parse(source)
    for needle in (
        "tenant A/B",
        "seed_replicon_beta_gold",
        "gold_consultor_mensual",
        "omega_refinement_gold",
        "forbidden Gold query",
        "tenant_id={other.tenant_id}",
        "workspace_id={other.workspace_id}",
        "x-workspace-id",
        "/api/intelligence/signals",
        "/api/control-room/dashboard",
        "/api/copilot/briefing/v2",
        "expected={403}",
        "tenant-ab-aws",
    ):
        assert needle in source


def test_aws_rollback_and_dr_wrappers_are_guarded() -> None:
    rollback = _read("scripts/aws_rollback.py")
    dr = _read("scripts/aws_dr_rehearsal.py")
    ast.parse(rollback)
    ast.parse(dr)

    assert "DEPLOY_REF_OLD or IMAGE_TAG_OLD is required" in rollback
    assert "rollback.sh" in rollback
    assert "RUN_BACKUP_BEFORE_ROLLBACK" in rollback
    assert "OMEGA_DR_REHEARSAL_EXECUTE=1" in dr
    assert "BLOCKED" in dr
