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


def test_aws_workspace_reads_gold_with_rls_scope() -> None:
    doc = yaml.safe_load(_read(AWS_COMPOSE))
    workspace = doc["services"]["workspace"]

    assert "GOLD_DATABASE_URL" in workspace["environment"]
    assert (
        "postgres_gold:5433/modecissions_gold"
        in workspace["environment"]["GOLD_DATABASE_URL"]
    )
    assert workspace["depends_on"]["postgres_gold"]["condition"] == "service_healthy"


def test_makefile_exposes_aws_beta_operational_targets() -> None:
    makefile = _read(MAKEFILE)
    for target, script in {
        "beta-smoke-aws:": "scripts/beta_smoke_aws.py",
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
    assert (
        ".env" not in source.split("remote_stdout_redacted.txt", 1)[0]
        or "env_value" in source
    )
    assert (
        "printenv" not in source or "CONTROL_ROOM_ENABLE_EXTERNAL_WRITEBACK" in source
    )


def test_schema_viewer_has_gold_dataset_fallback() -> None:
    source = _read("console/app/domains/data_platform/gold_catalog.py")
    router_source = _read("console/app/routers/v1/data.py")
    main_source = _read("console/app/main.py")
    for needle in (
        "async def sources_from_catalog",
        "OMEGA_HIDDEN_GOLD_SOURCE_CARTRIDGES",
        "simulation",
        "gold/",
        "async def schema_payload",
        "source_kind",
        "Gold database is not configured",
    ):
        assert needle in source
    assert "async def _gold_schema_payload" in main_source
    assert "async def _gold_sources_from_catalog" in main_source
    assert "_gold_schema_payload(source, user)" in router_source


def test_semantic_viewer_has_gold_catalog_fallback() -> None:
    source = _read("console/app/domains/data_platform/gold_catalog.py")
    main_source = ast.unparse(ast.parse(_read("console/app/main.py")))
    schema_js = _read("console/app/static/js/viewers/schema.js")
    for needle in (
        "async def semantic_entities_from_catalog",
        'source": "gold_catalog"',
        'self.schema_payload(f"gold/{dataset}"',
    ):
        assert needle in source
    assert "await _gold_semantic_entities_from_catalog(cartridge, user)" in main_source
    assert "if (!currentSource && sources.length)" in schema_js


def test_workspace_apps_prefer_direct_gold_for_published_data() -> None:
    source = _read("workspace/app/main.py")
    for needle in (
        "GOLD_DATABASE_URL",
        "def _visible_dataset_metadata",
        "def _query_gold_dataset_rows",
        "def _query_gold_dataset_options",
        "def _query_gold_dataset_filtered",
        "_user_with_dataset_scope",
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
    assert "isolated_temp_containers" in dr
    assert "docker run -d --name" in dr
    assert '"destructive": False' in dr
    assert "restore.sh" not in dr
