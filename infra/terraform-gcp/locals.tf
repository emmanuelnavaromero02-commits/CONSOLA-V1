locals {
  name_prefix = "omega-${var.environment}"

  labels = {
    project    = "modecissions"
    app        = "omega"
    env        = var.environment
    managed_by = "terraform"
  }

  public_domains_configured = var.public_console_domain != "" && var.public_workspace_domain != ""
  public_https_enabled      = var.enable_https && local.public_domains_configured

  public_domains = distinct([
    for domain in [
      var.public_console_domain,
      var.public_workspace_domain,
    ] : domain if domain != ""
  ])

  technical_console_url   = "http://${google_compute_global_address.public.address}"
  technical_workspace_url = "http://${google_compute_global_address.workspace.address}"

  console_public_url = (
    local.public_https_enabled
    ? "https://${var.public_console_domain}"
    : local.technical_console_url
  )

  workspace_public_url = (
    local.public_https_enabled
    ? "https://${var.public_workspace_domain}"
    : local.technical_workspace_url
  )

  airflow_public_url = "${local.console_public_url}/airflow"
  lakehouse_bucket   = var.lakehouse_bucket_name != "" ? var.lakehouse_bucket_name : google_storage_bucket.lakehouse.name

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
}
