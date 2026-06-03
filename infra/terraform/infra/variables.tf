variable "aws_region" {
  description = "AWS region for all resources"
  type        = string
  default     = "us-east-1"
}

variable "github_repo_url" {
  description = "SSH URL of the GitHub repo to clone on the App EC2 (e.g. git@github.com:org/modecissions.git)"
  type        = string
}

variable "deploy_ref" {
  description = "Immutable Git ref to checkout on the App EC2 (release tag or commit SHA)."
  type        = string
  validation {
    condition     = length(trimspace(var.deploy_ref)) > 0
    error_message = "deploy_ref is required for reproducible production deploys."
  }
}

variable "image_tag" {
  description = "Immutable GHCR image tag to deploy. Must match the release tag; do not use latest."
  type        = string
  validation {
    condition     = length(trimspace(var.image_tag)) > 0 && lower(var.image_tag) != "latest"
    error_message = "image_tag must be an immutable release tag, not latest."
  }
}

variable "key_pair_name" {
  description = "Name of the existing EC2 key pair to attach to both instances"
  type        = string
  default     = "modecissions-key"
}

variable "app_instance_type" {
  description = "EC2 instance type for the single-node app host."
  type        = string
  default     = "m6i.xlarge"
}

variable "vpn_instance_type" {
  description = "EC2 instance type for the VPN bastion."
  type        = string
  default     = "t3.nano"
}

variable "enable_vpn" {
  description = "Whether to run the WireGuard VPN bastion. Keep false for public ALB + SSM-only low-cost staging."
  type        = bool
  default     = true
}

variable "egress_mode" {
  description = "Private subnet outbound internet mode. nat_gateway is managed and pricier; nat_instance is cheaper and single-node."
  type        = string
  default     = "nat_gateway"

  validation {
    condition     = contains(["nat_gateway", "nat_instance"], var.egress_mode)
    error_message = "egress_mode must be nat_gateway or nat_instance."
  }
}

variable "nat_instance_type" {
  description = "EC2 instance type for low-cost NAT instance egress when egress_mode is nat_instance."
  type        = string
  default     = "t3.micro"
}

variable "app_env" {
  description = "Runtime APP_ENV injected into the EC2 app host. Use production only with public HTTPS configured."
  type        = string
  default     = "production"

  validation {
    condition     = contains(["production", "staging", "development"], lower(var.app_env))
    error_message = "app_env must be production, staging, or development."
  }
}

variable "public_console_domain" {
  description = "Public HTTPS hostname for the console, e.g. console.example.com. Leave empty for technical HTTP-only AWS validation."
  type        = string
  default     = ""

  validation {
    condition     = var.public_console_domain == "" || can(regex("^[A-Za-z0-9][A-Za-z0-9.-]+[A-Za-z0-9]$", var.public_console_domain))
    error_message = "public_console_domain must be empty or a DNS hostname, not a URL."
  }
}

variable "public_workspace_domain" {
  description = "Public HTTPS hostname for the workspace, e.g. workspace.example.com. Leave empty for technical HTTP-only AWS validation."
  type        = string
  default     = ""

  validation {
    condition     = var.public_workspace_domain == "" || can(regex("^[A-Za-z0-9][A-Za-z0-9.-]+[A-Za-z0-9]$", var.public_workspace_domain))
    error_message = "public_workspace_domain must be empty or a DNS hostname, not a URL."
  }
}

variable "route53_zone_id" {
  description = "Optional Route53 hosted zone ID for automatic ACM validation and DNS records. Leave empty to output manual ACM validation records."
  type        = string
  default     = ""
}

variable "manual_acm_validation_complete" {
  description = "Set true only after the manual ACM DNS validation records are created and the managed certificate is ISSUED. Used when route53_zone_id and public_acm_certificate_arn are empty."
  type        = bool
  default     = false
}

variable "public_acm_certificate_arn" {
  description = "Optional prevalidated ACM certificate ARN for the public ALB. Use when Route53 is managed outside this Terraform state."
  type        = string
  default     = ""
}

variable "ssh_allowed_cidrs" {
  description = "CIDRs allowed to reach SSH on the VPN bastion. Empty disables public SSH; use SSM by default."
  type        = list(string)
  default     = []

  validation {
    condition = alltrue([
      for cidr in var.ssh_allowed_cidrs :
      cidr != "0.0.0.0/0" && cidr != "::/0"
    ])
    error_message = "ssh_allowed_cidrs must not include 0.0.0.0/0 or ::/0."
  }
}

variable "alarm_email" {
  description = "Email address subscribed to v1 public CloudWatch alarms."
  type        = string
}

variable "enable_monthly_budget" {
  description = "Create an AWS Budgets monthly cost guardrail using alarm_email."
  type        = bool
  default     = true
}

variable "monthly_budget_limit_usd" {
  description = "Monthly AWS budget limit in USD for this account guardrail."
  type        = number
  default     = 50

  validation {
    condition     = var.monthly_budget_limit_usd > 0
    error_message = "monthly_budget_limit_usd must be greater than zero."
  }
}

variable "budget_actual_thresholds" {
  description = "Actual spend percentage thresholds that send budget emails."
  type        = list(number)
  default     = [50, 80, 100]

  validation {
    condition     = length(var.budget_actual_thresholds) > 0 && alltrue([for threshold in var.budget_actual_thresholds : threshold > 0])
    error_message = "budget_actual_thresholds must contain positive percentage values."
  }
}

variable "budget_forecast_thresholds" {
  description = "Forecasted spend percentage thresholds that send budget emails."
  type        = list(number)
  default     = [100]

  validation {
    condition     = alltrue([for threshold in var.budget_forecast_thresholds : threshold > 0])
    error_message = "budget_forecast_thresholds must contain positive percentage values."
  }
}

variable "smtp_host" {
  description = "SMTP host for transactional email. Use email-smtp.<region>.amazonaws.com for SES SMTP after domain approval."
  type        = string
  default     = "mailhog"
}

variable "email_provider" {
  description = "Transactional email provider. smtp uses SMTP_HOST; ses uses the EC2 IAM role and Amazon SES SendRawEmail."
  type        = string
  default     = "smtp"

  validation {
    condition     = contains(["smtp", "ses"], var.email_provider)
    error_message = "email_provider must be smtp or ses."
  }
}

variable "ses_sender_domain" {
  description = "Optional sender domain to verify in Amazon SES. Leave empty until the Google-managed domain is ready."
  type        = string
  default     = ""

  validation {
    condition     = var.ses_sender_domain == "" || can(regex("^[A-Za-z0-9][A-Za-z0-9.-]+[A-Za-z0-9]$", var.ses_sender_domain))
    error_message = "ses_sender_domain must be empty or a DNS hostname."
  }
}

variable "ses_mail_from_subdomain" {
  description = "MAIL FROM subdomain for SES bounce handling, e.g. mail for mail.example.com."
  type        = string
  default     = "mail"

  validation {
    condition     = can(regex("^[A-Za-z0-9][A-Za-z0-9-]*[A-Za-z0-9]$", var.ses_mail_from_subdomain))
    error_message = "ses_mail_from_subdomain must be a single DNS label."
  }
}

variable "smtp_port" {
  description = "SMTP port for transactional email."
  type        = number
  default     = 1025
}

variable "smtp_user" {
  description = "SMTP username. For SES SMTP this is the generated SMTP user, not the AWS access key id."
  type        = string
  default     = ""
  sensitive   = true
}

variable "smtp_from" {
  description = "Transactional email sender address."
  type        = string
  default     = "noreply@modecissions.local"
}

variable "smtp_from_domain" {
  description = "Sender domain used by the application for email links and diagnostics."
  type        = string
  default     = "modecissions.local"
}

variable "smtp_use_tls" {
  description = "Whether the application should issue STARTTLS for SMTP."
  type        = bool
  default     = false
}

variable "bedrock_model_ids" {
  description = "Bedrock foundation model IDs the app role may invoke."
  type        = list(string)
  default     = ["amazon.titan-embed-text-v2:0"]

  validation {
    condition = alltrue([
      for model_id in var.bedrock_model_ids :
      length(trimspace(model_id)) > 0
      && !strcontains(model_id, "*")
      && !strcontains(model_id, "/")
    ])
    error_message = "bedrock_model_ids must be non-empty Bedrock model IDs without wildcards or path separators."
  }
}

variable "bedrock_model_resource_arns" {
  description = "Optional additional Bedrock model or inference-profile ARNs the app role may invoke."
  type        = list(string)
  default     = []

  validation {
    condition = alltrue([
      for arn in var.bedrock_model_resource_arns :
      can(regex("^arn:aws[a-zA-Z-]*:bedrock:[a-z0-9-]+:[0-9]*:(foundation-model|inference-profile|application-inference-profile)/[^*]+$", arn))
    ])
    error_message = "bedrock_model_resource_arns must be specific Bedrock model/profile ARNs without wildcards."
  }
}

variable "vpn_admin_allowed_cidrs" {
  description = "CIDRs allowed to reach the wg-easy admin UI. Empty means no public admin UI ingress."
  type        = list(string)
  default     = []
  validation {
    condition = alltrue([
      for cidr in var.vpn_admin_allowed_cidrs :
      cidr != "0.0.0.0/0" && cidr != "::/0"
    ])
    error_message = "vpn_admin_allowed_cidrs must not include 0.0.0.0/0 or ::/0."
  }
}

variable "vpn_admin_password_hash" {
  description = "bcrypt password hash for the wg-easy admin UI. Pass a rotated hash through tfvars or TF_VAR_vpn_admin_password_hash."
  type        = string
  sensitive   = true

  validation {
    condition     = can(regex("^\\$2[aby]\\$[0-9]{2}\\$", var.vpn_admin_password_hash))
    error_message = "vpn_admin_password_hash must be a bcrypt hash such as $2b$12$..."
  }
}
