from __future__ import annotations

import re
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
TF = REPO / "infra/terraform/infra"
DEPLOY = REPO / "infra/terraform/deploy"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_public_https_variables_outputs_and_alb_exist():
    variables = _read(TF / "variables.tf")
    outputs = _read(TF / "outputs.tf")
    alb = _read(TF / "public_https.tf")

    for name in (
        "public_console_domain",
        "public_workspace_domain",
        "route53_zone_id",
        "manual_acm_validation_complete",
        "ssh_allowed_cidrs",
        "alarm_email",
    ):
        assert f'variable "{name}"' in variables

    for name in (
        "public_console_url",
        "public_workspace_url",
        "alb_dns_name",
        "managed_acm_certificate_arn",
        "ssm_app_command",
        "ssm_vpn_command",
        "ssm_wg_easy_port_forward_command",
        "acm_validation_records",
    ):
        assert f'output "{name}"' in outputs

    assert 'resource "aws_lb" "public"' in alb
    assert 'resource "aws_acm_certificate" "public"' in alb
    assert 'resource "aws_lb_listener" "http_redirect"' in alb
    assert 'resource "aws_lb_listener" "http_console_technical"' in alb
    assert 'resource "aws_lb_listener" "http_workspace_technical"' not in alb
    assert 'resource "aws_lb_listener" "https"' in alb
    assert "manual_acm_validation_complete" in alb
    assert 'path                = "/readyz"' in alb
    assert 'path                = "/healthz"' in alb
    assert 'values = [var.public_workspace_domain]' in alb


def test_terraform_has_two_public_subnets_for_alb():
    vpc = _read(TF / "vpc.tf")
    alb = _read(TF / "public_https.tf")
    assert 'resource "aws_subnet" "public"' in vpc
    assert 'resource "aws_subnet" "public_secondary"' in vpc
    assert 'aws_subnet.public.id, aws_subnet.public_secondary.id' in alb


def test_production_tfvars_pin_domains_certificate_waf_and_ses():
    tfvars = _read(TF / "terraform.tfvars.example")

    assert 'public_console_domain   = "console.7businesssolutions.com"' in tfvars
    assert 'public_workspace_domain = "workspace.7businesssolutions.com"' in tfvars
    assert (
        'public_acm_certificate_arn = "arn:aws:acm:us-east-1:095713296066:certificate/'
        in tfvars
    )
    assert 'enable_public_alb_waf             = true' in tfvars
    assert 'public_alb_waf_common_rule_action = "count"' in tfvars
    assert 'email_provider    = "ses"' in tfvars
    assert 'ses_sender_domain = "7businesssolutions.com"' in tfvars
    assert 'smtp_from        = "no-reply@7businesssolutions.com"' in tfvars
    assert 'smtp_from_domain = "7businesssolutions.com"' in tfvars


def test_no_public_ssh_or_internal_app_ports():
    sg = _read(TF / "security_groups.tf")
    assert "cidr_blocks = var.ssh_allowed_cidrs" in sg
    assert "SSH restricted to operator CIDRs" in sg

    ingress_blocks = re.findall(r'ingress\s+\{[\s\S]*?\n\s+\}', sg)

    ssh_world = [
        block for block in ingress_blocks
        if "SSH" in block and 'cidr_blocks = ["0.0.0.0/0"]' in block
    ]
    assert not ssh_world, "SSH must not be open to the world"

    for port in ("8000", "8001", "8081", "8082", "8088", "9000", "15432", "8201", "8202", "8203", "8204"):
        public_ingress = [
            block for block in ingress_blocks
            if re.search(rf"from_port\s*=\s*{port}\b", block)
            and 'cidr_blocks = ["0.0.0.0/0"]' in block
        ]
        assert not public_ingress, f"internal port {port} must not be public in Terraform security groups"


def test_public_alb_target_groups_only_use_internal_service_ports():
    alb = _read(TF / "public_https.tf")

    assert re.search(
        r'resource "aws_lb_target_group" "console" \{[\s\S]*?port\s*=\s*8000[\s\S]*?path\s*=\s*"/readyz"',
        alb,
    )
    assert re.search(
        r'resource "aws_lb_target_group" "workspace" \{[\s\S]*?port\s*=\s*8001[\s\S]*?path\s*=\s*"/healthz"',
        alb,
    )
    assert re.search(
        r'resource "aws_lb_target_group" "airflow" \{[\s\S]*?port\s*=\s*8082[\s\S]*?path\s*=\s*"/airflow/health"',
        alb,
    )
    assert 'resource "aws_lb_listener_rule" "airflow_path"' in alb
    assert 'values = ["/airflow", "/airflow/*"]' in alb
    for port in ("8081", "8088"):
        assert not re.search(rf'resource "aws_lb_target_group" "[^"]+" \{{[\s\S]*?port\s*=\s*{port}\b', alb)
        assert not re.search(rf'resource "aws_lb_listener" "[^"]+" \{{[\s\S]*?port\s*=\s*"{port}"', alb)
    assert not re.search(r'resource "aws_lb_listener" "[^"]+" \{[\s\S]*?port\s*=\s*"8082"', alb)


def test_waf_baseline_blocks_obvious_bad_traffic_and_observes_common_rules():
    waf = _read(TF / "waf.tf")
    variables = _read(TF / "variables.tf")

    assert 'resource "aws_wafv2_web_acl" "public_alb"' in waf
    assert 'resource "aws_wafv2_web_acl_association" "public_alb"' in waf
    assert "RateLimitPerIp" in waf
    assert "block {}" in waf
    assert "AWSManagedRulesAmazonIpReputationList" in waf
    assert "AWSManagedRulesKnownBadInputsRuleSet" in waf
    assert "AWSManagedRulesCommonRuleSet" in waf
    assert 'default     = "count"' in variables
    assert 'contains(["count", "block"], lower(var.public_alb_waf_common_rule_action))' in variables
    assert "cloudwatch_metrics_enabled = true" in waf


def test_phase_one_self_healing_uses_ec2_recover_not_horizontal_scaling():
    alb = _read(TF / "public_https.tf")
    ec2 = _read(TF / "ec2_app.tf")
    variables = _read(TF / "variables.tf")

    assert 'resource "aws_instance" "app"' in ec2
    assert 'resource "aws_cloudwatch_metric_alarm" "app_ec2_system_recover"' in alb
    assert "StatusCheckFailed_System" in alb
    assert 'arn:aws:automate:${var.aws_region}:ec2:recover' in alb
    assert "Do not replace with ASG until Postgres/state is externalized" in alb
    assert "aws_autoscaling_group" not in "".join(path.read_text(encoding="utf-8") for path in TF.glob("*.tf"))
    assert 'lower(var.image_tag) != "latest"' in variables


def test_userdata_sets_browser_urls_to_https_public_domains():
    userdata = _read(TF / "user_data/app.sh.tpl")
    assert "APP_ENV=${app_env}" in userdata
    assert "COOKIE_SECURE=${cookie_secure}" in userdata
    assert "CONSOLE_URL=${public_console_url}" in userdata
    assert "WORKSPACE_PUBLIC_URL=${public_workspace_url}" in userdata
    assert "APP_BASE_URL=${public_console_url}" in userdata
    assert "ALLOWED_ORIGINS=${public_console_url},${public_workspace_url}" in userdata
    assert "CONSOLE_URL=http://$APP_PRIVATE_IP:8000" not in userdata
    assert "WORKSPACE_PUBLIC_URL=http://$APP_PRIVATE_IP:8001" not in userdata


def test_release_gate_validates_terraform_and_public_closure_tests():
    workflow = _read(REPO / ".github/workflows/release.yml")
    assert "hashicorp/setup-terraform@v3" in workflow
    assert "terraform -chdir=infra/terraform/infra fmt -check" in workflow
    assert "terraform -chdir=infra/terraform/infra init -backend=false" in workflow
    assert "terraform -chdir=infra/terraform/infra validate" in workflow
    assert "tests/test_v1_public_https_closure.py" in workflow


def test_public_verify_and_operational_scripts_have_release_guards():
    verify = _read(REPO / "scripts/verify_v1_public.sh")
    backup = _read(DEPLOY / "backup.sh")
    restore = _read(DEPLOY / "restore.sh")
    rollback = _read(DEPLOY / "rollback.sh")

    assert "PUBLIC_CONSOLE_URL" in verify
    assert "E2E_LIVE_LLM=1 is required" in verify
    assert "TEST_PASSWORD or E2E_ADMIN_PASSWORD is required" in verify
    assert "login cookies are HttpOnly, Secure and SameSite" in verify
    assert "for port in 8000 8001 8081 8082 8088" in verify
    assert '/api/cartridges/${cartridge}/test_connection' in verify
    assert "npx playwright test" in verify

    assert "pg_dumpall -U postgres --clean --if-exists" in backup
    assert "manifest.json" in backup
    assert "aws s3 sync" in backup

    assert "CONFIRM_RESTORE=modecissions" in restore
    assert "BACKUP_ID" in restore
    assert "RESTORE_DELETE_PREFIX" in restore
    assert "unsafe RESTORE_DELETE_PREFIX" in restore
    assert "bash start.sh" in restore

    assert 'ROLLBACK_ARG="${1:-}"' in rollback
    assert 'TARGET_TAG="${ROLLBACK_ARG:-${IMAGE_TAG:-}}"' in rollback
    assert "RUN_BACKUP_BEFORE_ROLLBACK" in rollback
    assert "IMAGE_TAG" in rollback
    assert "bash update.sh" in rollback


def test_aws_deploy_env_documents_https_public_urls():
    env = _read(DEPLOY / ".env.example")
    compose = _read(DEPLOY / "docker-compose.aws.yml")
    entrypoint = _read(REPO / "scripts/aws-entrypoint.sh")
    assert "APP_ENV=production" in env
    assert "COOKIE_SECURE=true" in env
    assert "DEPLOY_REF=v1.0.0-rc3" in env
    assert "IMAGE_TAG=v1.0.0-rc3" in env
    assert "ALLOWED_ORIGINS=https://console.example.com,https://workspace.example.com" in env
    assert "CONSOLE_URL=https://console.example.com" in env
    assert "WORKSPACE_PUBLIC_URL=https://workspace.example.com" in env
    assert "AIRFLOW_PUBLIC_URL=https://console.example.com/airflow" in env
    assert "CONSOLE_URL=http://10.0.2.X:8000" not in env
    assert "WORKSPACE_PUBLIC_URL=http://10.0.2.X:8001" not in env
    assert "AIRFLOW_PUBLIC_URL=http://10.0.2.X:8082" not in env
    assert "COOKIE_SECURE:       ${COOKIE_SECURE:-true}" in compose
    assert "COOKIE_SECURE:        ${COOKIE_SECURE:-true}" in compose
    assert "$url_var must use https in production" in entrypoint


def test_aws_entrypoint_derives_cookie_security_from_public_scheme():
    entrypoint = _read(REPO / "scripts/aws-entrypoint.sh")

    assert 'PUBLIC_HTTPS_DEFAULT="false"' in entrypoint
    assert 'if [[ "$CONSOLE_URL" == https://* && "$WORKSPACE_PUBLIC_URL" == https://* ]]; then' in entrypoint
    assert 'PUBLIC_HTTPS_DEFAULT="true"' in entrypoint
    assert 'COOKIE_SECURE="${COOKIE_SECURE:-$PUBLIC_HTTPS_DEFAULT}"' in entrypoint
    assert 'SUPERSET_SESSION_COOKIE_SECURE="${SUPERSET_SESSION_COOKIE_SECURE:-$PUBLIC_HTTPS_DEFAULT}"' in entrypoint
    assert 'SUPERSET_FORCE_HTTPS="${SUPERSET_FORCE_HTTPS:-$PUBLIC_HTTPS_DEFAULT}"' in entrypoint
    assert "SUPERSET_SESSION_COOKIE_SECURE SUPERSET_FORCE_HTTPS SUPERSET_SESSION_COOKIE_SAMESITE" in entrypoint


def test_partial_dataset_badges_are_visible_in_catalog_ui():
    catalog_page = _read(REPO / "console-next/src/app/(shell)/data/catalog/page.tsx")
    inventory_page = _read(REPO / "console-next/src/app/(shell)/data/inventory/page.tsx")
    assert "DataTechnicalHub" in catalog_page
    assert "datasetReadiness" in inventory_page
    assert "Parcial" in inventory_page
    assert "AlertTriangle" in inventory_page
