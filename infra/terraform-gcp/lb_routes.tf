# Preserve the failed legacy certificate and its exact live identity. It stays
# attached only as inert rollback evidence through the HTTPS proxy lifecycle
# ignore below; this PR never replaces or deletes it.
resource "google_compute_managed_ssl_certificate" "public" {
  count = local.public_https_enabled ? 1 : 0
  name  = "${local.name_prefix}-public-cert"

  managed {
    domains = [
      "gcp-console.7businesssolutions.com",
      "gcp-workspace.7businesssolutions.com",
    ]
  }

  lifecycle {
    prevent_destroy = true
  }
}

# These two proxies previously routed plaintext directly to application
# backends. Retain their names and stable IP forwarders, but point both in
# place at the already-proven HTTPS redirect map.
resource "google_compute_target_http_proxy" "public" {
  name    = "${local.name_prefix}-http-proxy"
  url_map = google_compute_url_map.http_redirect[0].id
}

resource "google_compute_target_http_proxy" "public_redirect" {
  count   = local.public_https_enabled ? 1 : 0
  name    = "${local.name_prefix}-http-redirect-proxy"
  url_map = google_compute_url_map.http_redirect[0].id
}

resource "google_compute_target_http_proxy" "workspace" {
  name    = "${local.name_prefix}-workspace-http-proxy"
  url_map = google_compute_url_map.http_redirect[0].id
}

resource "google_compute_target_https_proxy" "public" {
  count           = local.public_https_enabled ? 1 : 0
  name            = "${local.name_prefix}-https-proxy"
  url_map         = google_compute_url_map.public.id
  certificate_map = local.certificate_manager_map_uri

  lifecycle {
    ignore_changes = [ssl_certificates]
  }
}

resource "google_compute_global_forwarding_rule" "http_redirect" {
  count                 = local.public_https_enabled ? 1 : 0
  name                  = "${local.name_prefix}-http-redirect"
  ip_address            = google_compute_global_address.public_https[0].id
  port_range            = "80"
  target                = google_compute_target_http_proxy.public_redirect[0].id
  load_balancing_scheme = "EXTERNAL_MANAGED"
}

resource "google_compute_global_forwarding_rule" "http" {
  name                  = "${local.name_prefix}-http"
  ip_address            = google_compute_global_address.public.id
  port_range            = "80"
  target                = google_compute_target_http_proxy.public.id
  load_balancing_scheme = "EXTERNAL_MANAGED"
}

resource "google_compute_global_forwarding_rule" "https" {
  count                 = local.public_https_enabled ? 1 : 0
  name                  = "${local.name_prefix}-https"
  ip_address            = google_compute_global_address.public_https[0].id
  port_range            = "443"
  target                = google_compute_target_https_proxy.public[0].id
  load_balancing_scheme = "EXTERNAL_MANAGED"
}

resource "google_compute_global_forwarding_rule" "workspace_http" {
  name                  = "${local.name_prefix}-workspace-http"
  ip_address            = google_compute_global_address.workspace.id
  port_range            = "80"
  target                = google_compute_target_http_proxy.workspace.id
  load_balancing_scheme = "EXTERNAL_MANAGED"
}
