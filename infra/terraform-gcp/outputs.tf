output "public_console_url" {
  description = "Public URL for the OMEGA console. Uses HTTPS domain when configured; otherwise technical HTTP IP."
  value       = local.console_public_url
}

output "public_airflow_url" {
  description = "Public URL for Airflow through the console load balancer path."
  value       = local.airflow_public_url
}

output "public_workspace_url" {
  description = "Public URL for the OMEGA workspace. Uses HTTPS domain when configured; otherwise technical HTTP IP."
  value       = local.workspace_public_url
}

output "technical_console_url" {
  description = "Technical HTTP IP URL for the console fallback."
  value       = local.technical_console_url
}

output "technical_workspace_url" {
  description = "Technical HTTP IP URL for the workspace fallback."
  value       = local.technical_workspace_url
}

output "public_https_ip" {
  description = "Dedicated HTTPS load balancer IP for the configured public domains."
  value       = local.public_https_enabled ? google_compute_global_address.public_https[0].address : null
}

output "public_https_enabled" {
  value = local.public_https_enabled
}

output "managed_certificate_name" {
  value = local.public_https_enabled ? google_compute_managed_ssl_certificate.public[0].name : null
}

output "app_instance_name" {
  value = google_compute_instance.app.name
}

output "app_zone" {
  value = var.zone
}

output "app_private_ip" {
  value = google_compute_instance.app.network_interface[0].network_ip
}

output "iap_ssh_command" {
  value = "gcloud compute ssh ${google_compute_instance.app.name} --project=${var.project_id} --zone=${var.zone} --tunnel-through-iap"
}

output "lakehouse_bucket" {
  value = local.lakehouse_bucket
}

output "artifact_registry_repository" {
  value = google_artifact_registry_repository.docker.name
}

output "source_sha" {
  value = var.source_sha
}

output "admin_credentials_path_on_vm" {
  value = "/opt/modecissions/admin_credentials.txt"
}

output "bigquery_talent_shadow" {
  description = "Keyless, non-serving Talent shadow runtime coordinates."
  value = var.provision_bigquery_talent_shadow ? {
    project_id      = var.bigquery_shadow_project_id
    dataset_id      = google_bigquery_dataset.talent_shadow[0].dataset_id
    service_account = google_service_account.talent_shadow[0].email
    runtime_enabled = var.bigquery_talent_shadow_runtime_enabled
  } : null
}

output "bigquery_talent_shadow_runtime_config" {
  description = "Non-secret, versioned handoff consumed atomically by the canonical deploy and first-boot paths."
  value = {
    schema_version        = 2
    deployment_project_id = var.project_id
    environment           = var.environment
    enabled               = var.provision_bigquery_talent_shadow && var.bigquery_talent_shadow_runtime_enabled
    project_id            = var.provision_bigquery_talent_shadow && var.bigquery_talent_shadow_runtime_enabled ? var.bigquery_shadow_project_id : ""
    dataset_id            = var.provision_bigquery_talent_shadow && var.bigquery_talent_shadow_runtime_enabled ? local.bigquery_shadow_dataset_id : ""
    location              = "us-central1"
    service_account       = var.provision_bigquery_talent_shadow && var.bigquery_talent_shadow_runtime_enabled ? google_service_account.talent_shadow[0].email : ""
    gold_bucket           = var.provision_bigquery_talent_shadow && var.bigquery_talent_shadow_runtime_enabled ? local.lakehouse_bucket : ""
    tenant_id             = var.provision_bigquery_talent_shadow && var.bigquery_talent_shadow_runtime_enabled ? lower(var.bigquery_shadow_main_tenant_id) : ""
    workspace_id          = var.provision_bigquery_talent_shadow && var.bigquery_talent_shadow_runtime_enabled ? lower(var.bigquery_shadow_main_workspace_id) : ""
    maximum_bytes_billed  = var.bigquery_shadow_maximum_bytes_billed
    population_backend    = "postgres_gold"
  }
}
