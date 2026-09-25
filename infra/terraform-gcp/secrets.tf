resource "google_secret_manager_secret" "runtime" {
  for_each = local.secret_names

  secret_id = "omega-${var.environment}-${each.value}"
  labels    = local.labels

  replication {
    auto {}
  }

  depends_on = [google_project_service.required]
}

resource "google_secret_manager_secret" "ghcr_pull_credentials" {
  secret_id = "omega-${var.environment}-ghcr_pull_credentials"
  labels    = local.labels

  replication {
    auto {}
  }

  depends_on = [google_project_service.required]
}
