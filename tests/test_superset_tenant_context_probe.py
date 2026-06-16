from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_superset_probe_scripts_are_parseable_and_redacted():
    for path in ("scripts/superset_tenant_probe.py", "scripts/aws_superset_probe.py"):
        src = _read(path)
        ast.parse(src)
        assert "redact(" in src
        assert "printenv" not in src
        assert ".env" not in src or ".env.example" in src or "env_value" in src


def test_superset_aws_probe_checks_public_and_internal_layers():
    src = _read("scripts/aws_superset_probe.py")
    for token in (
        "Public Superset direct access",
        "Superset internal health",
        "AWS Superset host binding",
        "127.0.0.1:8088:8088",
        "SUPERSET_PUBLIC_URL unset",
        "Tenant-facing Superset model",
        "Option A active",
    ):
        assert token in src
    assert "status in {403, 404}" in src
    assert "status={status}; Superset is publicly reachable" in src


def test_superset_probe_documents_db_roles_and_nobypassrls():
    src = _read("scripts/aws_superset_probe.py")
    for token in (
        "omega_refinement_gold",
        "omega_gold_reader",
        "omega_superset",
        "omega_superset_meta",
        "rolbypassrls=false",
        "Superset Gold grants documented",
        "Superset metastore grants documented",
        "Gold FORCE RLS",
    ):
        assert token in src


def test_superset_is_internal_by_default_in_aws_config():
    aws_compose = _read("infra/terraform/deploy/docker-compose.aws.yml")
    env_example = _read("infra/terraform/deploy/.env.example")
    entrypoint = _read("scripts/aws-entrypoint.sh")
    assert "127.0.0.1:8088:8088" in aws_compose
    assert '"8088:8088"' not in aws_compose
    assert "SUPERSET_PUBLIC_URL=" in env_example
    assert "10.0.2.X:8088" not in env_example
    assert 'SUPERSET_PUBLIC_URL="${SUPERSET_PUBLIC_URL:-}"' in entrypoint
    assert 'derive_public_url "$CONSOLE_URL" 8088' not in entrypoint


def test_superset_local_probe_encodes_option_a_contract():
    src = _read("scripts/superset_tenant_probe.py")
    for token in (
        "restricted/admin-only proved",
        "AWS Superset loopback binding",
        "AWS Superset public URL disabled by default",
        "Superset MCP tools are platform/admin gated",
        "No direct UI-only security assumption",
    ):
        assert token in src


def test_makefile_exposes_superset_tenant_probe_targets():
    makefile = _read("Makefile")
    assert "superset-tenant-probe:" in makefile
    assert "superset-tenant-probe-aws:" in makefile
    assert "scripts/superset_tenant_probe.py --target local" in makefile
    assert "scripts/superset_tenant_probe.py --target aws" in makefile
