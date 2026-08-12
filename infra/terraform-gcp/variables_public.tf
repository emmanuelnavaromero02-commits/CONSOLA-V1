variable "public_console_domain" {
  type        = string
  description = "Optional HTTPS console domain for the public load balancer."
  default     = ""
  validation {
    condition     = var.public_console_domain == "" || can(regex("^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\\.)+[a-z]([a-z0-9-]{0,61}[a-z0-9])?$", var.public_console_domain))
    error_message = "public_console_domain is invalid."
  }
}

variable "public_workspace_domain" {
  type        = string
  description = "Optional HTTPS workspace domain for the public load balancer host rule."
  default     = ""
  validation {
    condition     = var.public_workspace_domain == "" || can(regex("^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\\.)+[a-z]([a-z0-9-]{0,61}[a-z0-9])?$", var.public_workspace_domain))
    error_message = "public_workspace_domain is invalid."
  }
}

variable "enable_https" {
  type        = bool
  description = "Enable the canonical HTTPS proxy and HTTP redirect when public domains are configured."
  default     = true
}

variable "certificate_manager_map_name" {
  type        = string
  description = "Existing shared Certificate Manager map attached to the canonical HTTPS proxy."
  default     = ""
  validation {
    condition     = var.certificate_manager_map_name == "" || can(regex("^[a-z]([a-z0-9-]{0,61}[a-z0-9])?$", var.certificate_manager_map_name))
    error_message = "certificate_manager_map_name is invalid."
  }
}

variable "enable_lb_logging" {
  type        = bool
  description = "Enable request logging on public backend services."
  default     = true
}

variable "lb_log_sample_rate" {
  type        = number
  description = "Load balancer backend log sample rate from 0.0 to 1.0."
  default     = 1.0
  validation {
    condition     = var.lb_log_sample_rate >= 0 && var.lb_log_sample_rate <= 1
    error_message = "lb_log_sample_rate must be from 0 through 1."
  }
}

variable "enable_airflow_scheduler" {
  type        = bool
  description = "Keep the Airflow scheduler running on this GCP node."
  default     = true
}

variable "lakehouse_bucket_name" {
  type        = string
  description = "Optional existing GCS bucket name to use as the lakehouse. Defaults to this stack's bucket."
  default     = ""
  validation {
    condition     = var.lakehouse_bucket_name == "" || can(regex("^[a-z0-9][a-z0-9._-]{1,61}[a-z0-9]$", var.lakehouse_bucket_name))
    error_message = "lakehouse_bucket_name is invalid."
  }
}

variable "lakehouse_endpoint" {
  type        = string
  description = "S3-compatible endpoint used by DuckDB/MinIO clients for the GCS lakehouse."
  default     = "storage.googleapis.com"
  validation {
    condition     = can(regex("^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\\.)+[a-z]([a-z0-9-]{0,61}[a-z0-9])?$", var.lakehouse_endpoint))
    error_message = "lakehouse_endpoint must be a hostname without scheme or path."
  }
}

variable "enable_budget" {
  type        = bool
  description = "Create a monthly billing budget guardrail."
  default     = true
}

variable "monthly_budget_limit_usd" {
  type        = number
  description = "Monthly budget alert limit in monthly_budget_currency."
  default     = 100
  validation {
    condition     = var.monthly_budget_limit_usd > 0 && var.monthly_budget_limit_usd <= 1000000
    error_message = "monthly_budget_limit_usd is outside the reviewed bound."
  }
}

variable "monthly_budget_currency" {
  type        = string
  description = "Currency code for the monthly budget. Must match the billing account currency."
  default     = "USD"
  validation {
    condition     = can(regex("^[A-Z]{3}$", var.monthly_budget_currency))
    error_message = "monthly_budget_currency must be an ISO-style uppercase code."
  }
}

variable "cloud_armor_rate_limit_count" {
  type        = number
  description = "Requests per IP per minute before Cloud Armor rate limiting."
  default     = 300
  validation {
    condition     = var.cloud_armor_rate_limit_count >= 1 && var.cloud_armor_rate_limit_count <= 100000 && floor(var.cloud_armor_rate_limit_count) == var.cloud_armor_rate_limit_count
    error_message = "cloud_armor_rate_limit_count is outside the reviewed bound."
  }
}
