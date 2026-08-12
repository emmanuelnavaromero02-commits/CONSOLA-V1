resource "google_service_account" "app" {
  account_id   = "${local.name_prefix}-app"
  display_name = "OMEGA ${var.environment} app host"

  depends_on = [google_project_service.required]
}

resource "google_project_iam_member" "app_project_roles" {
  for_each = local.app_project_roles

  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.app.email}"
}

# Secret access is intentionally resource-scoped. In particular, the private
# GHCR credential must not require a project-wide Secret Manager grant.
resource "google_secret_manager_secret_iam_member" "app_runtime_secret_access" {
  for_each = {
    for name in local.app_host_secret_names :
    name => google_secret_manager_secret.runtime[name]
  }

  project   = var.project_id
  secret_id = each.value.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.app.email}"
}

resource "google_secret_manager_secret_iam_member" "app_ghcr_pull_credentials_access" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.ghcr_pull_credentials.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.app.email}"
}

resource "google_storage_bucket_iam_member" "app_lakehouse" {
  # The canonical bucket may be pre-existing.  Bind the effective reviewed
  # bucket, not the otherwise-unused Terraform-created default.
  bucket = local.lakehouse_bucket
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.app.email}"
}

resource "google_project_iam_custom_role" "release_backup_writer" {
  role_id     = "omegaReleaseBackupWriter"
  title       = "OMEGA release backup writer"
  description = "Create and verify release backup generations without list, overwrite, or delete"
  permissions = [
    "storage.buckets.get",
    "storage.objects.create",
    "storage.objects.get",
  ]
}

resource "google_storage_bucket_iam_member" "app_release_backup" {
  bucket = google_storage_bucket.release_backups.name
  role   = google_project_iam_custom_role.release_backup_writer.name
  member = "serviceAccount:${google_service_account.app.email}"
}

resource "google_storage_bucket_iam_member" "app_source" {
  bucket = var.source_bucket
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.app.email}"
}
