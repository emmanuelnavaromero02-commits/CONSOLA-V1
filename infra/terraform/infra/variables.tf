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

variable "public_console_domain" {
  description = "Public HTTPS hostname for the console, e.g. console.example.com."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9][A-Za-z0-9.-]+[A-Za-z0-9]$", var.public_console_domain))
    error_message = "public_console_domain must be a DNS hostname, not a URL."
  }
}

variable "public_workspace_domain" {
  description = "Public HTTPS hostname for the workspace, e.g. workspace.example.com."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9][A-Za-z0-9.-]+[A-Za-z0-9]$", var.public_workspace_domain))
    error_message = "public_workspace_domain must be a DNS hostname, not a URL."
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
