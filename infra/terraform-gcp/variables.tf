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
    condition     = can(regex("^[a-z]([a-z0-9-]{0,18}[a-z0-9])?$", var.environment))
    error_message = "environment is invalid."
  }
}

variable "canonical_writer" {
  type        = bool
  description = "Declare that this stack is the single canonical production writer."
  default     = true
  validation {
    condition     = var.canonical_writer
    error_message = "this module is canonical-writer-only; standby/technical stacks require a separate reviewed module."
  }
}

variable "revoke_project_secret_accessor" {
  type        = bool
  description = "Second-phase only: remove the legacy project-wide Secret Manager grant after the exact per-secret access transaction passes."
  default     = false
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

variable "controller_ref" {
  type        = string
  description = "Full reviewed Git SHA whose metadata-embedded bootstrap/controller helper bytes are rendered into startup_script. This is independent of the live source release SHA."
  validation {
    condition     = can(regex("^[0-9a-f]{40}$", var.controller_ref))
    error_message = "controller_ref must be one full lowercase Git SHA."
  }
}

variable "foundation_predecessor" {
  description = "Exact reviewed PR1 foundation receipt consumed by a later controller; null for the initial/same-controller handoff."
  type = object({
    marker_sha256         = string
    watchdog_state_sha256 = string
    deploy_ref            = string
    helper_ref            = string
    startup_config_sha256 = string
  })
  default  = null
  nullable = true

  validation {
    condition = var.foundation_predecessor == null || (
      can(regex("^[0-9a-f]{64}$", var.foundation_predecessor.marker_sha256)) &&
      can(regex("^[0-9a-f]{64}$", var.foundation_predecessor.watchdog_state_sha256)) &&
      can(regex("^[0-9a-f]{40}$", var.foundation_predecessor.deploy_ref)) &&
      can(regex("^[0-9a-f]{40}$", var.foundation_predecessor.helper_ref)) &&
      can(regex("^[0-9a-f]{64}$", var.foundation_predecessor.startup_config_sha256))
    )
    error_message = "foundation_predecessor must be null or one exact reviewed foundation receipt identity."
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
    condition     = var.source_size_bytes > 0 && var.source_size_bytes <= 2147483648 && floor(var.source_size_bytes) == var.source_size_bytes
    error_message = "source_size_bytes must be a positive integer no larger than 2 GiB."
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

variable "boot_image" {
  type        = string
  description = "Reviewed immutable Ubuntu 22.04 GCE image self-link; image families are forbidden."
  validation {
    condition     = can(regex("^projects/ubuntu-os-cloud/global/images/ubuntu-2204-jammy-v[0-9]{8}$", var.boot_image))
    error_message = "boot_image must be one immutable ubuntu-2204-jammy-vYYYYMMDD image self-link."
  }
}
