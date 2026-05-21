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
