locals {
  name_prefix = "omega-${var.environment}"

  labels = {
    project    = "modecissions"
    app        = "omega"
    env        = var.environment
    managed_by = "terraform"
  }

  app_project_roles = toset(concat(
    [
      "roles/artifactregistry.reader",
      "roles/logging.logWriter",
      "roles/monitoring.metricWriter",
    ],
    var.revoke_project_secret_accessor ? [] : ["roles/secretmanager.secretAccessor"],
  ))

  public_domains_configured = var.public_console_domain != "" && var.public_workspace_domain != ""
  public_https_enabled      = var.enable_https && local.public_domains_configured
  certificate_manager_map_uri = (
    local.public_https_enabled
    ? "//certificatemanager.googleapis.com/projects/${var.project_id}/locations/global/certificateMaps/${var.certificate_manager_map_name}"
    : ""
  )

  public_domains = distinct([
    for domain in [
      var.public_console_domain,
      var.public_workspace_domain,
    ] : domain if domain != ""
  ])

  technical_console_url   = ""
  technical_workspace_url = ""

  console_public_url = (
    "https://${var.public_console_domain}"
  )

  workspace_public_url = (
    "https://${var.public_workspace_domain}"
  )

  airflow_public_url    = "${local.console_public_url}/airflow"
  lakehouse_bucket      = var.lakehouse_bucket_name != "" ? var.lakehouse_bucket_name : google_storage_bucket.lakehouse.name
  release_backup_bucket = google_storage_bucket.release_backups.name

  # Version the effective reboot/bootstrap controller. The provider's
  # metadata_startup_script field is ForceNew, so the day-2 controller must
  # reconcile these exact rendered bytes in place with metadata fingerprint
  # CAS and verify this hash by read-back without replacing the writer VM.
  startup_contract = {
    schema_version         = 1
    project_id             = var.project_id
    environment            = var.environment
    controller_ref         = var.controller_ref
    foundation_predecessor = var.foundation_predecessor
    # The startup controller revalidates all five values against the GCE
    # metadata server before publishing the root-owned host identity contract.
    host_identity = {
      project_id            = var.project_id
      instance_id           = "4767392334132429161"
      instance_name         = "${local.name_prefix}-app"
      zone                  = var.zone
      service_account_email = google_service_account.app.email
    }
    source = {
      bucket         = var.source_bucket
      object         = var.source_object
      ref            = var.source_sha
      generation     = var.source_generation
      size_bytes     = var.source_size_bytes
      archive_sha256 = var.source_archive_sha256
    }
    urls = {
      public_console      = local.console_public_url
      public_workspace    = local.workspace_public_url
      public_airflow      = local.airflow_public_url
      technical_console   = local.technical_console_url
      technical_workspace = local.technical_workspace_url
    }
    admin_email              = var.admin_email
    cookie_secure            = local.public_https_enabled
    data_disk_size_bytes     = var.data_disk_size_gb * 1073741824
    host_package_versions    = var.host_package_versions
    secret_versions          = var.secret_versions
    lakehouse_bucket         = local.lakehouse_bucket
    release_backup_bucket    = local.release_backup_bucket
    lakehouse_endpoint       = var.lakehouse_endpoint
    canonical_writer         = var.canonical_writer
    enable_airflow_scheduler = var.enable_airflow_scheduler
    # PR1 intentionally cannot bootstrap any application runtime. A later
    # foundation PR may change this only together with exact digest authority,
    # pre-pull/local-ID proof and pull_policy=never for all 26 services.
    exact_runtime_contract_ready = false
    secret_prefix                = "omega-${var.environment}-"
    compose_override             = templatefile("${path.module}/templates/docker-compose.gcp.yml.tftpl", {})
  }

  startup_script = templatefile("${path.module}/templates/startup.sh.tftpl", {
    startup_config_base64     = base64encode(jsonencode(local.startup_contract))
    bootstrap_runtime_base64  = base64gzip(file("${path.module}/../../scripts/gcp/bootstrap-runtime.sh"))
    metadata_firewall_base64  = base64gzip(file("${path.module}/../../scripts/gcp/metadata-firewall.sh"))
    operation_guard_base64    = base64gzip(file("${path.module}/templates/omega-operation-gate"))
    operation_watchdog_base64 = base64gzip(file("${path.module}/../../scripts/gcp/operation-watchdog.sh"))
    reboot_runtime_base64     = base64gzip(file("${path.module}/../../scripts/gcp/reboot-runtime.sh"))
    runtime_contract_base64   = base64gzip(file("${path.module}/../../scripts/gcp/runtime_contract.py"))
    safe_io_base64            = base64gzip(file("${path.module}/../../scripts/gcp/safe_io.py"))
  })

  required_services = toset([
    "artifactregistry.googleapis.com",
    "billingbudgets.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "compute.googleapis.com",
    "iam.googleapis.com",
    "iap.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
    "oslogin.googleapis.com",
    "secretmanager.googleapis.com",
    "serviceusage.googleapis.com",
    "storage.googleapis.com",
  ])

  secret_names = toset([
    "anthropic_api_key",
    "jwt_secret_key",
    "internal_api_key",
    "security_context_signing_key",
    "control_room_evidence_signing_key_id",
    "control_room_evidence_signing_key",
    "control_room_evidence_signing_previous_keys",
    "internal_api_key_console_to_console",
    "internal_api_key_console_to_refinement",
    "internal_api_key_console_to_vault",
    "internal_api_key_console_to_mcp_infra",
    "internal_api_key_mcp_infra_to_console",
    "internal_api_key_console_to_cartridge",
    "internal_api_key_airflow_to_cartridge",
    "internal_api_key_workspace_to_console",
    "internal_api_key_workspace_to_refinement",
    "internal_api_key_workspace_to_mcp_infra",
    "internal_api_key_airflow_to_mcp_infra",
    "internal_api_key_airflow_to_refinement",
    "internal_api_key_airflow_to_console",
    "internal_api_key_refinement_to_mcp_infra",
    "internal_api_key_replicon_to_console",
    "internal_api_key_replicon_to_mcp_infra",
    "internal_api_key_replicon_to_refinement",
    "internal_api_key_hubspot_to_console",
    "internal_api_key_hubspot_to_mcp_infra",
    "internal_api_key_hubspot_to_refinement",
    "internal_api_key_salesforce_to_console",
    "internal_api_key_banxico_to_console",
    "internal_api_key_inegi_to_console",
    "internal_api_key_sec_edgar_to_console",
    "internal_api_key_sap_hcm_to_console",
    "internal_api_key_sap_s4hana_to_console",
    "internal_api_key_sap_successfactors_to_console",
    "internal_api_key_mcp_infra_to_vault",
    "internal_api_key_cartridge_to_console",
    "internal_api_key_cartridge_to_refinement",
    "internal_api_key_workspace_to_vault",
    "internal_api_key_refinement_to_vault",
    "postgres_password",
    "field_encryption_key",
    "vault_encryption_key",
    "omega_console_password",
    "omega_refinement_password",
    "omega_vault_password",
    "omega_workspace_password",
    "omega_mcp_infra_password",
    "omega_refinement_gold_password",
    "omega_airflow_dag_password",
    "omega_airflow_meta_password",
    "omega_superset_meta_password",
    "omega_cartridge_sap_hcm_password",
    "omega_cartridge_sap_s4_password",
    "omega_cartridge_sap_sf_password",
    "omega_cartridge_replicon_password",
    "omega_cartridge_salesforce_password",
    "omega_cartridge_hubspot_password",
    "omega_cartridge_banxico_password",
    "omega_cartridge_inegi_password",
    "omega_cartridge_sec_edgar_password",
    "airflow_secret_key",
    "airflow_admin_password",
    "agent_runner_token",
    "superset_secret_key",
    "superset_admin_password",
    "superset_service_password",
    "smtp_password",
    "gcs_hmac_access_key_id",
    "gcs_hmac_secret_access_key",
  ])

  # The VM reads only these five runtime secrets directly. Application secrets
  # stay container-scoped; granting the whole 69-secret inventory to the host
  # would recreate the project-wide privilege this release removes.
  app_host_secret_names = toset([
    "control_room_evidence_signing_key_id",
    "control_room_evidence_signing_key",
    "control_room_evidence_signing_previous_keys",
    "gcs_hmac_access_key_id",
    "gcs_hmac_secret_access_key",
  ])
}
