resource "google_service_account" "app" {
  account_id   = "${local.name_prefix}-app"
  display_name = "OMEGA ${var.environment} app host"

  depends_on = [google_project_service.required]
}

resource "google_project_iam_member" "app_project_roles" {
  for_each = toset([
    "roles/artifactregistry.reader",
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
  ])

  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.app.email}"
}

# Secret access is intentionally resource-scoped. In particular, the private
# GHCR credential must not require a project-wide Secret Manager grant.
resource "google_secret_manager_secret_iam_member" "app_runtime_secret_access" {
  for_each = google_secret_manager_secret.runtime

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
  bucket = google_storage_bucket.lakehouse.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.app.email}"
}

resource "google_storage_bucket_iam_member" "app_source" {
  bucket = var.source_bucket
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.app.email}"
}
