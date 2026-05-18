locals {
  modecissions_app_secret_names = toset([
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
    "SUPERSET_SERVICE_PASSWORD",
    "GITHUB_DEPLOY_KEY",
  ])
}

resource "aws_secretsmanager_secret" "app" {
  for_each = local.modecissions_app_secret_names

  name        = "modecissions/${lower(each.key)}"
  description = "MODecissions runtime secret for ${each.key}"
}

output "modecissions_secret_arns" {
  description = "ARNs injected into the EC2 app host by aws-entrypoint.sh"
  value       = { for key, secret in aws_secretsmanager_secret.app : key => secret.arn }
  sensitive   = true
}
