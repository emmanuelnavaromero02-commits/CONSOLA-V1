variable "host_package_versions" {
  type        = map(string)
  description = "Reviewed exact apt package versions for the immutable Ubuntu 22.04 jammy host and Docker repository."

  validation {
    condition = (
      toset(keys(var.host_package_versions)) == toset([
        "ca_certificates",
        "containerd_io",
        "curl",
        "docker_buildx_plugin",
        "docker_ce",
        "docker_ce_cli",
        "docker_compose_plugin",
        "gnupg",
        "iptables",
        "jq",
        "lsof",
        "openssl",
        "python3",
        ]) && alltrue([
        for version in values(var.host_package_versions) :
        length(version) >= 3 && length(version) <= 160 &&
        can(regex("^[0-9A-Za-z][0-9A-Za-z.+:~_-]+$", version))
      ])
    )
    error_message = "host_package_versions must contain exactly thirteen bounded literal apt versions."
  }
}

variable "secret_versions" {
  type        = map(string)
  description = "Reviewed numeric Secret Manager versions; values never enter Terraform state. HMAC keys are both empty or both pinned."
  default = {
    control_room_evidence_signing_key_id        = ""
    control_room_evidence_signing_key           = ""
    control_room_evidence_signing_previous_keys = ""
    gcs_hmac_access_key_id                      = ""
    gcs_hmac_secret_access_key                  = ""
  }

  validation {
    condition = (
      toset(keys(var.secret_versions)) == toset([
        "control_room_evidence_signing_key_id",
        "control_room_evidence_signing_key",
        "control_room_evidence_signing_previous_keys",
        "gcs_hmac_access_key_id",
        "gcs_hmac_secret_access_key",
      ]) &&
      alltrue([
        for name in [
          "control_room_evidence_signing_key_id",
          "control_room_evidence_signing_key",
          "control_room_evidence_signing_previous_keys",
        ] : can(regex("^[1-9][0-9]*$", var.secret_versions[name]))
      ]) &&
      (
        (var.secret_versions.gcs_hmac_access_key_id == "" && var.secret_versions.gcs_hmac_secret_access_key == "") ||
        (
          can(regex("^[1-9][0-9]*$", var.secret_versions.gcs_hmac_access_key_id)) &&
          can(regex("^[1-9][0-9]*$", var.secret_versions.gcs_hmac_secret_access_key))
        )
      )
    )
    error_message = "required secret versions must be exact numeric IDs; the optional GCS HMAC pair must be both absent or both numeric."
  }
}

variable "boot_disk_size_gb" {
  type        = number
  description = "Boot disk size in GB."
  default     = 60
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
    condition     = can(regex("^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$", var.admin_email)) && length(var.admin_email) <= 254
    error_message = "admin_email is invalid."
  }
}
