check "source_object_matches_sha" {
  assert {
    condition     = var.source_object == "deploy-artifacts/${var.source_sha}/repo.tar.gz"
    error_message = "source_object must be derived from source_sha exactly."
  }
}

check "zone_matches_region" {
  assert {
    condition     = startswith(var.zone, "${var.region}-")
    error_message = "zone must belong to region."
  }
}

check "public_domain_pair" {
  assert {
    condition     = (var.public_console_domain == "") == (var.public_workspace_domain == "")
    error_message = "public domains must be configured or omitted as a pair."
  }
}

check "public_domains_are_distinct" {
  assert {
    condition     = var.public_console_domain == "" || var.public_console_domain != var.public_workspace_domain
    error_message = "console and workspace public domains must be distinct."
  }
}

check "canonical_https_contract" {
  assert {
    condition = !local.public_https_enabled || (
      var.public_console_domain == "console.7businesssolutions.com" &&
      var.public_workspace_domain == "workspace.7businesssolutions.com" &&
      var.certificate_manager_map_name == "sevenbs-production-map"
    )
    error_message = "canonical HTTPS requires the exact console/workspace runtime domains and shared sevenbs-production-map."
  }
}

check "public_edge_mode_is_explicit" {
  assert {
    condition     = local.public_https_enabled
    error_message = "the canonical-only module requires exact public HTTPS; technical HTTP is deliberately unmanaged."
  }
}

check "canonical_writer_requires_https" {
  assert {
    condition     = !var.canonical_writer || local.public_https_enabled
    error_message = "the canonical writer requires the exact HTTPS domain/map contract."
  }
}

check "canonical_scheduler" {
  assert {
    condition     = var.enable_airflow_scheduler == var.canonical_writer
    error_message = "the scheduler must be enabled only on the single canonical writer and disabled everywhere else."
  }
}
