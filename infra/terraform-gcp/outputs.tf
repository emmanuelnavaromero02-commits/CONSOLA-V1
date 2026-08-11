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

output "source_bucket" {
  value = var.source_bucket
}

output "source_object" {
  value = var.source_object
}

output "startup_script_sha256" {
  description = "Expected SHA-256 of the effective GCE metadata startup script; verify by read-back before day-2 deploy."
  value       = sha256(local.startup_script)
}

output "admin_credentials_path_on_vm" {
  value = "/opt/modecissions/admin_credentials.txt"
}
