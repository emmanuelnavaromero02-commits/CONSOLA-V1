resource "google_storage_bucket" "lakehouse" {
  name                        = "${local.name_prefix}-lakehouse-${var.project_number}"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false
  labels                      = local.labels

  versioning {
    enabled = true
  }

  lifecycle_rule {
    condition {
      age = 30
    }
    action {
      type          = "SetStorageClass"
      storage_class = "NEARLINE"
    }
  }

  lifecycle_rule {
    condition {
      num_newer_versions = 5
      with_state         = "ARCHIVED"
    }
    action {
      type = "Delete"
    }
  }

  depends_on = [google_project_service.required]
}

resource "google_artifact_registry_repository" "docker" {
  location      = var.region
  repository_id = "${local.name_prefix}-docker"
  description   = "OMEGA container images for ${var.environment}"
  format        = "DOCKER"
  labels        = local.labels

  cleanup_policies {
    id     = "delete-untagged-after-14d"
    action = "DELETE"

    condition {
      tag_state  = "UNTAGGED"
      older_than = "1209600s"
    }
  }

  depends_on = [google_project_service.required]
}
