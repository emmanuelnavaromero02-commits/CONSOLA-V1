resource "google_secret_manager_secret" "runtime" {
  for_each = local.secret_names

  secret_id = "omega-${var.environment}-${each.value}"
  labels    = local.labels

  replication {
    auto {}
  }

  depends_on = [google_project_service.required]
}

# Terraform owns only the container. Operators add a JSON secret version
# (username + token) out of band so the credential never enters source,
# tfvars, plans, or Terraform state.
resource "google_secret_manager_secret" "ghcr_pull_credentials" {
  secret_id = "omega-${var.environment}-ghcr_pull_credentials"
  labels    = local.labels

  replication {
    auto {}
  }

  depends_on = [google_project_service.required]
}
