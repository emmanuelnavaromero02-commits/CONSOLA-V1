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
    "OMEGA_CONSOLE_PASSWORD",
    "OMEGA_REFINEMENT_PASSWORD",
    "OMEGA_VAULT_PASSWORD",
    "OMEGA_WORKSPACE_PASSWORD",
    "OMEGA_MCP_INFRA_PASSWORD",
    "OMEGA_REFINEMENT_GOLD_PASSWORD",
    "OMEGA_AIRFLOW_DAG_PASSWORD",
    "OMEGA_AIRFLOW_META_PASSWORD",
    "AIRFLOW_SECRET_KEY",
    "AIRFLOW_ADMIN_PASSWORD",
    "SUPERSET_SECRET_KEY",
    "SUPERSET_ADMIN_PASSWORD",
    "GITHUB_DEPLOY_KEY",
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_secretsmanager_tf_declares_all_required_secrets():
    src = _read(TF / "secretsmanager.tf")
    assert 'resource "aws_secretsmanager_secret" "app"' in src
    assert 'resource "aws_secretsmanager_secret_version"' not in src
    assert "secret_string" not in src
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
    assert "optional_secrets" in src
    assert "GHCR_OWNER" in src
    assert "IMAGE_TAG" in src
    assert "CONSOLE_URL" in src
    assert "WORKSPACE_PUBLIC_URL" in src
    assert 'printf \'%s="%s' in src


def test_compose_aws_does_not_contain_secret_literals():
    src = _read(DEPLOY / "docker-compose.aws.yml")
    leaked_assignments = re.findall(
        r"(?m)^\s+(JWT_SECRET_KEY|INTERNAL_API_KEY|POSTGRES_PASSWORD|ANTHROPIC_API_KEY|GEMINI_API_KEY|SMTP_PASSWORD):\s+[A-Za-z0-9_./+=-]{8,}\s*$",
        src,
    )
    assert leaked_assignments == []
    assert "aws-entrypoint.sh" in src


def test_deploy_key_is_not_passed_through_terraform_state():
    variables = _read(TF / "variables.tf")
    ec2 = _read(TF / "ec2_app.tf")
    userdata = _read(TF / "user_data/app.sh.tpl")
    assert "deploy_private_key" not in variables
    assert "deploy_private_key" not in ec2
    assert "${deploy_private_key}" not in userdata
    assert "GITHUB_DEPLOY_KEY" in ec2
    assert "secretsmanager get-secret-value" in userdata


def test_aws_env_example_does_not_document_static_aws_keys():
    src = _read(DEPLOY / ".env.example")
    assert "AWS_ACCESS_KEY_ID=" not in src
    assert "AWS_SECRET_ACCESS_KEY=" not in src
    assert "instance profile" in src


def test_deploy_runbook_mentions_secretsmanager_not_nano_env():
    src = _read(DEPLOY / "DEPLOY-RUNBOOK.md")
    assert "AWS Secrets Manager" in src
    assert "scripts/aws-entrypoint.sh" in src
    assert "nano .env" not in src
    assert "deploy_private_key" not in src
    assert "terraform apply -target=aws_secretsmanager_secret.app" in src
