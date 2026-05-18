locals {
  modecissions_app_secrets = {
    ANTHROPIC_API_KEY    = var.anthropic_api_key
    GEMINI_API_KEY       = var.gemini_api_key
    JWT_SECRET_KEY       = var.jwt_secret
    INTERNAL_API_KEY     = var.internal_api_key
    POSTGRES_PASSWORD    = var.postgres_password
    FIELD_ENCRYPTION_KEY = var.field_encryption_key
    SMTP_PASSWORD        = var.smtp_password
  }
}

resource "aws_secretsmanager_secret" "app" {
  for_each = local.modecissions_app_secrets

  name        = "modecissions/${lower(each.key)}"
  description = "MODecissions runtime secret for ${each.key}"
}

resource "aws_secretsmanager_secret_version" "app" {
  for_each = local.modecissions_app_secrets

  secret_id     = aws_secretsmanager_secret.app[each.key].id
  secret_string = each.value
}

output "modecissions_secret_arns" {
  description = "ARNs injected into the EC2 app host by aws-entrypoint.sh"
  value       = { for key, secret in aws_secretsmanager_secret.app : key => secret.arn }
  sensitive   = true
}
