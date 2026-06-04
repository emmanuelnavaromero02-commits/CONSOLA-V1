from __future__ import annotations

from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (REPO / path).read_text(encoding="utf-8")


def test_deploy_workflow_uses_manual_approval_oidc_and_ssm():
    workflow = _read(".github/workflows/deploy-aws.yml")
    assert "workflow_dispatch:" in workflow
    assert "environment: production" in workflow
    assert "id-token: write" in workflow
    assert "aws-actions/configure-aws-credentials@v4" in workflow
    assert "AWS_DEPLOY_ROLE_ARN" in workflow
    assert "AWS-RunShellScript" in workflow
    assert "aws ssm send-command" in workflow
    assert "aws ssm get-command-invocation" in workflow
    assert "aws-production-deploy" in workflow


def test_deploy_workflow_requires_immutable_ref_and_readiness():
    workflow = _read(".github/workflows/deploy-aws.yml")
    assert "deploy_ref must be a published release tag" in workflow
    assert "image_tag must be a published GHCR release tag" in workflow
    assert "deploy_ref and image_tag must match" in workflow
    assert 'set_env_line("APP_ENV", "production")' in workflow
    assert "DEPLOY_REF" in workflow
    assert "IMAGE_TAG" in workflow
    assert "/healthz" in workflow
    assert "/readyz" in workflow
    assert "/readyz?require_data=1" in workflow


def test_terraform_defines_optional_github_actions_deploy_role():
    variables = _read("infra/terraform/infra/variables.tf")
    oidc = _read("infra/terraform/infra/github_actions_deploy.tf")
    outputs = _read("infra/terraform/infra/outputs.tf")

    assert 'variable "enable_github_actions_deploy_role"' in variables
    assert 'variable "github_actions_deploy_repo"' in variables
    assert 'variable "github_actions_deploy_environment"' in variables
    assert 'resource "aws_iam_openid_connect_provider" "github_actions"' in oidc
    assert "token.actions.githubusercontent.com:sub" in oidc
    assert "sts:AssumeRoleWithWebIdentity" in oidc
    assert "ssm:SendCommand" in oidc
    assert "AWS-RunShellScript" in oidc
    assert "ec2:DescribeInstances" in oidc
    assert 'output "github_actions_deploy_role_arn"' in outputs


def test_scheduled_aws_health_monitor_exists():
    workflow = _read(".github/workflows/monitor-aws-health.yml")
    script = _read("scripts/monitor_health_once.sh")
    makefile = _read("Makefile")

    assert "cron:" in workflow
    assert "scripts/monitor_health_once.sh" in workflow
    assert "OMEGA_MONITOR_REQUIRE_DATA" in workflow
    assert "/healthz" in script
    assert "/readyz?require_data=1" in script
    assert "monitor-check:" in makefile
