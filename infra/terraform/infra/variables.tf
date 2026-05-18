variable "aws_region" {
  description = "AWS region for all resources"
  type        = string
  default     = "us-east-1"
}

variable "postgres_password" {
  description = "Password for the in-container PostgreSQL on the App EC2"
  type        = string
  sensitive   = true
}

variable "github_repo_url" {
  description = "SSH URL of the GitHub repo to clone on the App EC2 (e.g. git@github.com:org/modecissions.git)"
  type        = string
}

variable "deploy_private_key" {
  description = "Contents of the GitHub deploy private key to install on the App EC2"
  type        = string
  sensitive   = true
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
