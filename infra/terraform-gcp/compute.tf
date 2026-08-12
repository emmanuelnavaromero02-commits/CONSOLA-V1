resource "google_compute_disk" "docker_data" {
  name   = "${local.name_prefix}-docker-data"
  type   = "pd-balanced"
  zone   = var.zone
  size   = var.data_disk_size_gb
  labels = local.labels

  physical_block_size_bytes = 4096

  lifecycle {
    prevent_destroy = true
  }
}

resource "google_compute_instance" "app" {
  name         = "${local.name_prefix}-app"
  machine_type = var.app_machine_type
  zone         = var.zone
  tags         = ["${local.name_prefix}-app"]
  labels       = local.labels

  allow_stopping_for_update = true
  # Match the exact live VM until boot/deletion hardening receives its own
  # reviewed maintenance window. Changing either value can stop the writer.
  deletion_protection = false

  boot_disk {
    auto_delete = true
    initialize_params {
      image = var.boot_image
      size  = var.boot_disk_size_gb
      type  = "pd-balanced"
    }
  }

  attached_disk {
    source      = google_compute_disk.docker_data.id
    device_name = "omega-docker-data"
    mode        = "READ_WRITE"
  }

  network_interface {
    subnetwork = google_compute_subnetwork.app.id
  }

  metadata = {
    enable-oslogin = "TRUE"
  }

  metadata_startup_script = local.startup_script

  # The provider marks metadata_startup_script ForceNew. Replacing the
  # canonical writer VM merely to refresh reboot metadata is forbidden.
  # A reviewed day-2 controller performs startup-script updates in place
  # through Compute setMetadata with the live fingerprint as a CAS.
  lifecycle {
    ignore_changes  = [metadata_startup_script]
    prevent_destroy = true

    precondition {
      # GCE metadata values are limited to 256 KiB. Base64 length is used so
      # the check measures UTF-8 bytes, not Terraform Unicode characters.
      condition     = length(base64encode(local.startup_script)) <= 349524
      error_message = "Rendered startup-script must remain below the 262144-byte GCE metadata limit."
    }
  }

  service_account {
    email  = google_service_account.app.email
    scopes = ["https://www.googleapis.com/auth/cloud-platform"]
  }

  shielded_instance_config {
    enable_secure_boot          = true
    enable_vtpm                 = true
    enable_integrity_monitoring = true
  }

  depends_on = [
    google_compute_router_nat.main,
    google_storage_bucket_iam_member.app_source,
    google_storage_bucket_iam_member.app_release_backup,
    google_secret_manager_secret_iam_member.app_runtime_secret_access,
    google_secret_manager_secret_iam_member.app_ghcr_pull_credentials_access,
    google_project_iam_member.app_project_roles,
  ]
}

resource "google_compute_instance_group" "app" {
  name      = "${local.name_prefix}-app-ig"
  zone      = var.zone
  instances = [google_compute_instance.app.id]

  named_port {
    name = "console"
    port = 8000
  }

  named_port {
    name = "workspace"
    port = 8001
  }

  named_port {
    name = "airflow"
    port = 8082
  }
}
