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
    "INTERNAL_API_KEY_CONSOLE_TO_CONSOLE",
    "INTERNAL_API_KEY_CONSOLE_TO_REFINEMENT",
    "INTERNAL_API_KEY_CONSOLE_TO_VAULT",
    "INTERNAL_API_KEY_CONSOLE_TO_MCP_INFRA",
    "INTERNAL_API_KEY_CONSOLE_TO_CARTRIDGE",
    "INTERNAL_API_KEY_AIRFLOW_TO_CARTRIDGE",
    "INTERNAL_API_KEY_WORKSPACE_TO_CONSOLE",
    "INTERNAL_API_KEY_WORKSPACE_TO_REFINEMENT",
    "INTERNAL_API_KEY_WORKSPACE_TO_MCP_INFRA",
    "INTERNAL_API_KEY_AIRFLOW_TO_MCP_INFRA",
    "INTERNAL_API_KEY_AIRFLOW_TO_REFINEMENT",
    "INTERNAL_API_KEY_AIRFLOW_TO_CONSOLE",
    "INTERNAL_API_KEY_REFINEMENT_TO_MCP_INFRA",
    "INTERNAL_API_KEY_REPLICON_TO_CONSOLE",
    "INTERNAL_API_KEY_REPLICON_TO_MCP_INFRA",
    "INTERNAL_API_KEY_REPLICON_TO_REFINEMENT",
    "INTERNAL_API_KEY_HUBSPOT_TO_CONSOLE",
    "INTERNAL_API_KEY_HUBSPOT_TO_MCP_INFRA",
    "INTERNAL_API_KEY_HUBSPOT_TO_REFINEMENT",
    "INTERNAL_API_KEY_MCP_INFRA_TO_VAULT",
    "INTERNAL_API_KEY_CARTRIDGE_TO_CONSOLE",
    "INTERNAL_API_KEY_CARTRIDGE_TO_REFINEMENT",
    "INTERNAL_API_KEY_WORKSPACE_TO_VAULT",
    "INTERNAL_API_KEY_REFINEMENT_TO_VAULT",
    "POSTGRES_PASSWORD",
    "FIELD_ENCRYPTION_KEY",
    "VAULT_ENCRYPTION_KEY",
    "SMTP_PASSWORD",
    "OMEGA_CONSOLE_PASSWORD",
    "OMEGA_REFINEMENT_PASSWORD",
    "OMEGA_VAULT_PASSWORD",
    "OMEGA_WORKSPACE_PASSWORD",
    "OMEGA_MCP_INFRA_PASSWORD",
    "OMEGA_REFINEMENT_GOLD_PASSWORD",
    "OMEGA_AIRFLOW_DAG_PASSWORD",
    "OMEGA_AIRFLOW_META_PASSWORD",
    "OMEGA_SUPERSET_META_PASSWORD",
    "OMEGA_CARTRIDGE_SAP_HCM_PASSWORD",
    "OMEGA_CARTRIDGE_SAP_S4_PASSWORD",
    "OMEGA_CARTRIDGE_SAP_SF_PASSWORD",
    "OMEGA_CARTRIDGE_REPLICON_PASSWORD",
    "OMEGA_CARTRIDGE_HUBSPOT_PASSWORD",
    "AIRFLOW_SECRET_KEY",
    "AIRFLOW_ADMIN_PASSWORD",
    "AGENT_RUNNER_TOKEN",
    "SUPERSET_SECRET_KEY",
    "SUPERSET_ADMIN_PASSWORD",
    "SUPERSET_SERVICE_PASSWORD",
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
    required_block = re.search(r"required_secrets=\(([\s\S]*?)\)\n\noptional_secrets=", src)
    assert required_block
    assert "SUPERSET_SERVICE_PASSWORD" in required_block.group(1)
    assert "GHCR_OWNER" in src
    assert "IMAGE_TAG" in src
    assert "CONSOLE_URL" in src
    assert "WORKSPACE_PUBLIC_URL" in src
    assert "AIRFLOW_PUBLIC_URL" in src
    assert "SUPERSET_PUBLIC_URL" in src
    assert "must not point to localhost in production" in src
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


def test_terraform_requires_immutable_deploy_ref_and_image_tag():
    variables = _read(TF / "variables.tf")
    ec2 = _read(TF / "ec2_app.tf")
    userdata = _read(TF / "user_data/app.sh.tpl")
    assert 'variable "deploy_ref"' in variables
    assert 'variable "image_tag"' in variables
    assert 'lower(var.image_tag) != "latest"' in variables
    assert re.search(r"deploy_ref\s+=\s+var\.deploy_ref", ec2)
    assert re.search(r"image_tag\s+=\s+var\.image_tag", ec2)
    assert "DEPLOY_REF=${deploy_ref}" in userdata
    assert "IMAGE_TAG=${image_tag}" in userdata
    assert "IMAGE_TAG=v1.44.5" not in userdata


def test_aws_env_example_does_not_document_static_aws_keys():
    src = _read(DEPLOY / ".env.example")
    assert "AWS_ACCESS_KEY_ID=" not in src
    assert "AWS_SECRET_ACCESS_KEY=" not in src
    assert "instance profile" in src
    assert "INTERNAL_API_KEY_CONSOLE_TO_CONSOLE=" in src
    assert "SAP_HCM_BASE_URL=" in src
    assert "HUBSPOT_BASE_URL=" in src
    assert "HUBSPOT_URL=" in src
    assert "SF_TOKEN_URL=" in src


def test_deploy_runbook_mentions_secretsmanager_not_nano_env():
    src = _read(DEPLOY / "DEPLOY-RUNBOOK.md")
    assert "AWS Secrets Manager" in src
    assert "scripts/aws-entrypoint.sh" in src
    assert "nano .env" not in src
    assert "deploy_private_key" not in src
    assert "terraform apply -target=aws_secretsmanager_secret.app" in src
