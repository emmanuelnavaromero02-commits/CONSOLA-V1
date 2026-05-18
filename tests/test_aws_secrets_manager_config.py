from __future__ import annotations

import re
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
TF = REPO / "infra/terraform/infra"
DEPLOY = REPO / "infra/terraform/deploy"


REQUIRED_SECRET_NAMES = {
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "JWT_SECRET_KEY",
    "INTERNAL_API_KEY",
    "POSTGRES_PASSWORD",
    "FIELD_ENCRYPTION_KEY",
    "SMTP_PASSWORD",
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_secretsmanager_tf_declares_all_required_secrets():
    src = _read(TF / "secretsmanager.tf")
    assert 'resource "aws_secretsmanager_secret" "app"' in src
    assert 'resource "aws_secretsmanager_secret_version" "app"' in src
    for secret_name in REQUIRED_SECRET_NAMES:
        assert secret_name in src


def test_iam_policy_uses_arn_specific_resources():
    src = _read(TF / "iam.tf")
    block = re.search(
        r'data\s+"aws_iam_policy_document"\s+"app_secretsmanager"\s+\{(?P<body>[\s\S]*?)\n\}',
        src,
    )
    assert block, "missing app_secretsmanager policy document"
    body = block.group("body")
    assert "secretsmanager:GetSecretValue" in body
    assert 'resources = ["*"]' not in body
    assert "aws_secretsmanager_secret.app" in body
    assert "secret.arn" in body


def test_aws_entrypoint_script_fail_fast_on_missing_secret():
    src = _read(REPO / "scripts/aws-entrypoint.sh")
    assert "set -Eeuo pipefail" in src
    assert "umask 077" in src
    assert "missing ARN env var" in src
    assert "exit 1" in src
    assert "secretsmanager get-secret-value" in src
    assert "sleep \"$delay\"" in src


def test_compose_aws_does_not_contain_secret_literals():
    src = _read(DEPLOY / "docker-compose.aws.yml")
    leaked_assignments = re.findall(
        r"(?m)^\s+(JWT_SECRET_KEY|INTERNAL_API_KEY|POSTGRES_PASSWORD|ANTHROPIC_API_KEY|GEMINI_API_KEY|SMTP_PASSWORD):\s+[A-Za-z0-9_./+=-]{8,}\s*$",
        src,
    )
    assert leaked_assignments == []
    assert "aws-entrypoint.sh" in src


def test_deploy_runbook_mentions_secretsmanager_not_nano_env():
    src = _read(DEPLOY / "DEPLOY-RUNBOOK.md")
    assert "AWS Secrets Manager" in src
    assert "scripts/aws-entrypoint.sh" in src
    assert "nano .env" not in src
