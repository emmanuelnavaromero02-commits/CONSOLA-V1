locals {
  bigquery_shadow_dataset_id = "omega_${replace(var.environment, "-", "_")}_talent_shadow"
  bigquery_shadow_sa_id = format(
    "omega-bq-%s-%s",
    substr(replace(var.environment, "_", "-"), 0, 12),
    substr(md5(var.bigquery_shadow_project_id), 0, 6),
  )
}

resource "google_project_service" "bigquery_shadow" {
  for_each = var.provision_bigquery_talent_shadow ? toset([
    "bigquery.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "serviceusage.googleapis.com",
  ]) : toset([])

  project            = var.bigquery_shadow_project_id
  service            = each.value
  disable_on_destroy = false

  lifecycle {
    precondition {
      condition = (
        var.bigquery_shadow_project_id != "" &&
        var.bigquery_shadow_project_id != var.project_id
      )
      error_message = "The BigQuery shadow pilot requires a dedicated project so its daily quota cannot throttle serving workloads."
    }
  }
}

data "google_storage_bucket" "bigquery_shadow_gold" {
  count = var.provision_bigquery_talent_shadow ? 1 : 0
  name  = local.lakehouse_bucket

  depends_on = [google_storage_bucket.lakehouse]
}

resource "google_bigquery_dataset" "talent_shadow" {
  count = var.provision_bigquery_talent_shadow ? 1 : 0

  project                     = var.bigquery_shadow_project_id
  dataset_id                  = local.bigquery_shadow_dataset_id
  friendly_name               = "OMEGA Talent 9-Box shadow (${var.environment})"
  description                 = "Ephemeral, non-serving parity tables. PostgreSQL Gold remains authoritative."
  location                    = "us-central1"
  default_table_expiration_ms = 86400000
  max_time_travel_hours       = 48
  delete_contents_on_destroy  = false

  labels = merge(local.labels, {
    workload = "talent-9box-shadow"
    serving  = "false"
    pii      = "restricted"
  })

  # Supplying the complete access list prevents BigQuery from installing its
  # default projectReaders/projectWriters grants. The shadow table contains
  # row-level Talent identifiers, so project basic roles must never inherit
  # dataset access. Project owners retain the minimum ownership entry required
  # to administer the dataset; the keyless shadow identity is the only editor.
  access {
    role          = "OWNER"
    special_group = "projectOwners"
  }

  access {
    role          = "WRITER"
    user_by_email = google_service_account.talent_shadow[0].email
  }

  lifecycle {
    precondition {
      condition     = var.region == "us-central1"
      error_message = "The Talent shadow pilot is fixed to us-central1."
    }
    precondition {
      condition     = can(regex("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$", var.bigquery_shadow_main_workspace_id))
      error_message = "bigquery_shadow_main_workspace_id must be the Main Workspace UUID."
    }
    precondition {
      condition     = can(regex("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$", var.bigquery_shadow_main_tenant_id))
      error_message = "bigquery_shadow_main_tenant_id must be the tenant UUID that owns Main Workspace."
    }
  }

  depends_on = [google_project_service.bigquery_shadow]
}

resource "google_service_account" "talent_shadow" {
  count = var.provision_bigquery_talent_shadow ? 1 : 0

  project      = var.bigquery_shadow_project_id
  account_id   = local.bigquery_shadow_sa_id
  display_name = "OMEGA Talent BigQuery shadow"
  description  = "Keyless identity for the non-serving Talent 9-Box parity pilot."

  depends_on = [google_project_service.bigquery_shadow]
}

# No google_service_account_key resource is permitted. The VM identity obtains
# short-lived credentials by impersonating the dedicated shadow identity.
resource "google_service_account_iam_member" "talent_shadow_impersonation" {
  count = var.provision_bigquery_talent_shadow ? 1 : 0

  service_account_id = google_service_account.talent_shadow[0].name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${google_service_account.app.email}"
}

resource "google_project_iam_member" "talent_shadow_job_user" {
  count = var.provision_bigquery_talent_shadow ? 1 : 0

  project = var.bigquery_shadow_project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.talent_shadow[0].email}"
}

resource "google_storage_bucket_iam_member" "talent_shadow_gold_reader" {
  count = var.provision_bigquery_talent_shadow ? 1 : 0

  bucket = local.lakehouse_bucket
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.talent_shadow[0].email}"

  condition {
    title       = "read_main_workspace_talent_9box_snapshots"
    description = "Object GET only for immutable 9-Box Gold snapshots in the canary tenant/workspace; bucket listing does not match."
    expression  = "resource.name.startsWith('projects/_/buckets/${local.lakehouse_bucket}/objects/gold/sap_successfactors/sap_successfactors_talent_9box/tenant_id=${lower(var.bigquery_shadow_main_tenant_id)}/workspace_id=${lower(var.bigquery_shadow_main_workspace_id)}/_snapshots/_pending/')"
  }

  lifecycle {
    precondition {
      condition     = data.google_storage_bucket.bigquery_shadow_gold[0].uniform_bucket_level_access
      error_message = "Conditional object IAM requires uniform bucket-level access."
    }
    precondition {
      condition     = upper(data.google_storage_bucket.bigquery_shadow_gold[0].location) == "US-CENTRAL1"
      error_message = "The Gold bucket and BigQuery shadow dataset must both be in us-central1."
    }
  }
}

# BigQuery load jobs also require storage.buckets.get. A project custom role,
# granted only on this one bucket, avoids bucket listing and every write/admin
# permission while the conditional objectViewer binding above controls object
# GETs at the exact tenant/workspace prefix.
resource "google_project_iam_custom_role" "talent_shadow_bucket_get" {
  count = var.provision_bigquery_talent_shadow ? 1 : 0

  project     = var.project_id
  role_id     = "omega_talent_shadow_bucket_get_${replace(var.environment, "-", "_")}"
  title       = "OMEGA Talent shadow bucket metadata reader"
  description = "Allows only storage.buckets.get for BigQuery Gold loads."
  permissions = ["storage.buckets.get"]
}

resource "google_storage_bucket_iam_member" "talent_shadow_bucket_get" {
  count = var.provision_bigquery_talent_shadow ? 1 : 0

  bucket = local.lakehouse_bucket
  role   = google_project_iam_custom_role.talent_shadow_bucket_get[0].name
  member = "serviceAccount:${google_service_account.talent_shadow[0].email}"
}

# This override is project-wide, hence the hard requirement for a dedicated
# shadow project above. 0.25 TiB/day is the initial pilot ceiling.
resource "google_service_usage_consumer_quota_override" "talent_shadow_daily_query" {
  count    = var.provision_bigquery_talent_shadow ? 1 : 0
  provider = google-beta

  project        = var.bigquery_shadow_project_id
  service        = "bigquery.googleapis.com"
  metric         = urlencode("bigquery.googleapis.com/quota/query/usage")
  limit          = urlencode("/d/project")
  override_value = tostring(var.bigquery_shadow_daily_query_quota_mib)
  force          = true

  depends_on = [google_project_service.bigquery_shadow]
}
