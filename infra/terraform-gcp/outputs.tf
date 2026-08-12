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
  description = "Technical HTTP console URL only in explicit domainless staging mode."
  value       = local.technical_console_url != "" ? local.technical_console_url : null
}

output "technical_workspace_url" {
  description = "Technical HTTP workspace URL only in explicit domainless staging mode."
  value       = local.technical_workspace_url != "" ? local.technical_workspace_url : null
}

output "public_https_ip" {
  description = "Dedicated HTTPS load balancer IP for the configured public domains."
  value       = local.public_https_enabled ? google_compute_global_address.public_https[0].address : null
}

output "public_https_enabled" {
  value = local.public_https_enabled
}

output "legacy_compute_certificate_name" {
  value = local.public_https_enabled ? "${local.name_prefix}-public-cert" : null
}

output "certificate_manager_map_uri" {
  value = local.public_https_enabled ? local.certificate_manager_map_uri : null
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

output "release_backup_bucket" {
  value = local.release_backup_bucket
}

output "artifact_registry_repository" {
  value = google_artifact_registry_repository.docker.name
}

output "source_sha" {
  value = var.source_sha
}

output "source_bucket" {
  value = var.source_bucket
}

output "source_object" {
  value = var.source_object
}

output "startup_script_sha256" {
  description = "Expected SHA-256 of the reviewed startup render; adopt with metadata CAS and verify by live read-back."
  value       = sha256(local.startup_script)
}

output "startup_script_base64" {
  description = "Exact reviewed startup bytes consumed only by the sealed metadata-CAS transaction."
  value       = base64encode(local.startup_script)
  sensitive   = true
}

output "admin_credentials_path_on_vm" {
  value = "/opt/modecissions/admin_credentials.txt"
}
