resource "google_compute_disk" "docker_data" {
  name   = "${local.name_prefix}-docker-data"
  type   = "pd-balanced"
  zone   = var.zone
  size   = var.data_disk_size_gb
  labels = local.labels

  physical_block_size_bytes = 4096
}

resource "google_compute_instance" "app" {
  name         = "${local.name_prefix}-app"
  machine_type = var.app_machine_type
  zone         = var.zone
  tags         = ["${local.name_prefix}-app"]
  labels       = local.labels

  allow_stopping_for_update = true

  boot_disk {
    auto_delete = true
    initialize_params {
      image = "ubuntu-os-cloud/ubuntu-2204-lts"
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

  metadata_startup_script = templatefile("${path.module}/templates/startup.sh.tftpl", {
    project_id               = var.project_id
    source_bucket            = var.source_bucket
    source_object            = var.source_object
    source_sha               = var.source_sha
    public_console_url       = local.console_public_url
    public_workspace_url     = local.workspace_public_url
    public_airflow_url       = local.airflow_public_url
    technical_console_url    = local.technical_console_url
    technical_workspace_url  = local.technical_workspace_url
    admin_email              = var.admin_email
    cookie_secure            = local.public_https_enabled ? "true" : "false"
    lakehouse_bucket         = local.lakehouse_bucket
    lakehouse_endpoint       = var.lakehouse_endpoint
    enable_airflow_scheduler = var.enable_airflow_scheduler ? "true" : "false"
    secret_prefix            = "omega-${var.environment}-"
  })

  lifecycle {
    ignore_changes = [metadata_startup_script]
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
