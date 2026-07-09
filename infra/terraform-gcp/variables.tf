variable "project_id" {
  type        = string
  description = "Google Cloud project id."
}

variable "project_number" {
  type        = string
  description = "Google Cloud project number, used for billing budget filters."
}

variable "billing_account_id" {
  type        = string
  description = "Billing account id in billingAccounts/XXXX format."
}

variable "region" {
  type        = string
  description = "Primary Google Cloud region."
  default     = "us-central1"
}

variable "zone" {
  type        = string
  description = "Primary Google Cloud zone."
  default     = "us-central1-a"
}

variable "environment" {
  type        = string
  description = "Deployment environment label."
  default     = "staging"
}

variable "source_bucket" {
  type        = string
  description = "Private GCS bucket containing the exact main source tarball."
}

variable "source_object" {
  type        = string
  description = "GCS object name for the source tarball."
}

variable "source_sha" {
  type        = string
  description = "Git commit SHA represented by source_object."
}

variable "app_machine_type" {
  type        = string
  description = "Compute Engine machine type for the single-node beta host."
  default     = "e2-standard-4"
}

variable "boot_disk_size_gb" {
  type        = number
  description = "Boot disk size in GB."
  default     = 30
}

variable "data_disk_size_gb" {
  type        = number
  description = "Persistent Docker data disk size in GB."
  default     = 150
}

variable "admin_email" {
  type        = string
  description = "Bootstrap admin email created after first boot."
  default     = "emmanuelromero060601@gmail.com"
}

variable "public_console_domain" {
  type        = string
  description = "Optional HTTPS console domain for the public load balancer."
  default     = ""
}

variable "public_workspace_domain" {
  type        = string
  description = "Optional HTTPS workspace domain for the public load balancer host rule."
  default     = ""
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
}

variable "lakehouse_endpoint" {
  type        = string
  description = "S3-compatible endpoint used by DuckDB/MinIO clients for the GCS lakehouse."
  default     = "storage.googleapis.com"
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
}

variable "monthly_budget_currency" {
  type        = string
  description = "Currency code for the monthly budget. Must match the billing account currency."
  default     = "USD"
}

variable "cloud_armor_rate_limit_count" {
  type        = number
  description = "Requests per IP per minute before Cloud Armor rate limiting."
  default     = 300
}
