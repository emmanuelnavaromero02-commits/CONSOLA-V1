variable "project_id" {
  type        = string
  description = "Google Cloud project id."
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", var.project_id))
    error_message = "project_id must be one canonical GCP project id."
  }
}

variable "project_number" {
  type        = string
  description = "Google Cloud project number, used for billing budget filters."
  validation {
    condition     = can(regex("^[1-9][0-9]{5,19}$", var.project_number))
    error_message = "project_number must be numeric."
  }
}

variable "billing_account_id" {
  type        = string
  description = "Billing account id in billingAccounts/XXXX format."
  validation {
    condition     = can(regex("^[0-9A-F]{6}-[0-9A-F]{6}-[0-9A-F]{6}$", var.billing_account_id))
    error_message = "billing_account_id must use XXXXXX-XXXXXX-XXXXXX format."
  }
}

variable "region" {
  type        = string
  description = "Primary Google Cloud region."
  default     = "us-central1"
  validation {
    condition     = can(regex("^[a-z]+-[a-z]+[0-9]$", var.region))
    error_message = "region is invalid."
  }
}

variable "zone" {
  type        = string
  description = "Primary Google Cloud zone."
  default     = "us-central1-a"
  validation {
    condition     = can(regex("^[a-z]+-[a-z]+[0-9]-[a-z]$", var.zone))
    error_message = "zone is invalid."
  }
}

variable "environment" {
  type        = string
  description = "Deployment environment label."
  default     = "staging"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{0,29}$", var.environment))
    error_message = "environment is invalid."
  }
}

variable "source_bucket" {
  type        = string
  description = "Private GCS bucket containing the exact main source tarball."
  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9._-]{1,61}[a-z0-9]$", var.source_bucket))
    error_message = "source_bucket is invalid."
  }
}

variable "source_object" {
  type        = string
  description = "GCS object name for the source tarball."
  validation {
    condition     = can(regex("^deploy-artifacts/[0-9a-f]{40}/repo\\.tar\\.gz$", var.source_object))
    error_message = "source_object must be one ref-addressed release archive."
  }
}

variable "source_sha" {
  type        = string
  description = "Git commit SHA represented by source_object."
  validation {
    condition     = can(regex("^[0-9a-f]{40}$", var.source_sha))
    error_message = "source_sha must be one full lowercase Git SHA."
  }
}

variable "source_generation" {
  type        = string
  description = "Exact immutable GCS generation of source_object."
  validation {
    condition     = can(regex("^[1-9][0-9]*$", var.source_generation))
    error_message = "source_generation must be a positive integer string."
  }
}

variable "source_size_bytes" {
  type        = number
  description = "Exact byte size of source_object."
  validation {
    condition     = var.source_size_bytes > 0 && floor(var.source_size_bytes) == var.source_size_bytes
    error_message = "source_size_bytes must be a positive integer."
  }
}

variable "source_archive_sha256" {
  type        = string
  description = "Exact SHA-256 of source_object bytes."
  validation {
    condition     = can(regex("^[0-9a-f]{64}$", var.source_archive_sha256))
    error_message = "source_archive_sha256 must be an exact lowercase SHA-256."
  }
}

variable "app_machine_type" {
  type        = string
  description = "Compute Engine machine type for the single-node beta host."
  default     = "e2-standard-4"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,62}$", var.app_machine_type))
    error_message = "app_machine_type is invalid."
  }
}

variable "boot_disk_size_gb" {
  type        = number
  description = "Boot disk size in GB."
  default     = 30
  validation {
    condition     = var.boot_disk_size_gb >= 30 && var.boot_disk_size_gb <= 2048 && floor(var.boot_disk_size_gb) == var.boot_disk_size_gb
    error_message = "boot_disk_size_gb must be an integer from 30 through 2048."
  }
}

variable "data_disk_size_gb" {
  type        = number
  description = "Persistent Docker data disk size in GB."
  default     = 150
  validation {
    condition     = var.data_disk_size_gb >= 10 && var.data_disk_size_gb <= 65536 && floor(var.data_disk_size_gb) == var.data_disk_size_gb
    error_message = "data_disk_size_gb must be a bounded positive integer."
  }
}

variable "admin_email" {
  type        = string
  description = "Bootstrap admin email created after first boot."
  default     = "emmanuelromero060601@gmail.com"
  validation {
    condition     = can(regex("^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(\\.[A-Za-z0-9-]+)+$", var.admin_email)) && length(var.admin_email) <= 254
    error_message = "admin_email is invalid."
  }
}

variable "public_console_domain" {
  type        = string
  description = "Optional HTTPS console domain for the public load balancer."
  default     = ""
  validation {
    condition     = var.public_console_domain == "" || can(regex("^[a-z0-9]([a-z0-9-]{0,62}\\.)+[a-z]{2,63}$", var.public_console_domain))
    error_message = "public_console_domain is invalid."
  }
}

variable "public_workspace_domain" {
  type        = string
  description = "Optional HTTPS workspace domain for the public load balancer host rule."
  default     = ""
  validation {
    condition     = var.public_workspace_domain == "" || can(regex("^[a-z0-9]([a-z0-9-]{0,62}\\.)+[a-z]{2,63}$", var.public_workspace_domain))
    error_message = "public_workspace_domain is invalid."
  }
}

variable "enable_https" {
  type        = bool
  description = "Enable managed certificate, HTTPS proxy, and HTTP redirect when public domains are configured."
  default     = true
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
    condition     = can(regex("^[a-z0-9]([a-z0-9-]{0,62}\\.)+[a-z]{2,63}$", var.lakehouse_endpoint))
    error_message = "lakehouse_endpoint must be a hostname without scheme or path."
  }
}

check "source_object_matches_sha" {
  assert {
    condition     = var.source_object == "deploy-artifacts/${var.source_sha}/repo.tar.gz"
    error_message = "source_object must be derived from source_sha exactly."
  }
}

check "zone_matches_region" {
  assert {
    condition     = startswith(var.zone, "${var.region}-")
    error_message = "zone must belong to region."
  }
}

check "public_domain_pair" {
  assert {
    condition     = (var.public_console_domain == "") == (var.public_workspace_domain == "")
    error_message = "public domains must be configured or omitted as a pair."
  }
}

check "canonical_scheduler" {
  assert {
    condition     = var.enable_airflow_scheduler
    error_message = "the canonical GCP writer must keep exactly one scheduler enabled."
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
