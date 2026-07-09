resource "google_compute_global_address" "public" {
  name   = "${local.name_prefix}-public-ip"
  labels = local.labels
}

resource "google_compute_global_address" "public_https" {
  count  = local.public_https_enabled ? 1 : 0
  name   = "${local.name_prefix}-public-https-ip"
  labels = local.labels
}

resource "google_compute_global_address" "workspace" {
  name   = "${local.name_prefix}-workspace-ip"
  labels = local.labels
}

resource "google_compute_security_policy" "public" {
  name        = "${local.name_prefix}-cloud-armor"
  description = "Low-cost OMEGA public edge policy."

  rule {
    priority    = 1000
    action      = "rate_based_ban"
    description = "Per-IP rate limit."

    match {
      versioned_expr = "SRC_IPS_V1"
      config {
        src_ip_ranges = ["*"]
      }
    }

    rate_limit_options {
      conform_action   = "allow"
      exceed_action    = "deny(429)"
      enforce_on_key   = "IP"
      ban_duration_sec = 300

      rate_limit_threshold {
        count        = var.cloud_armor_rate_limit_count
        interval_sec = 60
      }
    }
  }

  rule {
    priority    = 2147483647
    action      = "allow"
    description = "Default allow."

    match {
      versioned_expr = "SRC_IPS_V1"
      config {
        src_ip_ranges = ["*"]
      }
    }
  }
}

resource "google_compute_health_check" "console" {
  name               = "${local.name_prefix}-console-health"
  check_interval_sec = 15
  timeout_sec        = 5

  http_health_check {
    port         = 8000
    request_path = "/healthz"
  }
}

resource "google_compute_health_check" "workspace" {
  name               = "${local.name_prefix}-workspace-health"
  check_interval_sec = 15
  timeout_sec        = 5

  http_health_check {
    port         = 8001
    request_path = "/healthz"
  }
}

resource "google_compute_health_check" "airflow" {
  name               = "${local.name_prefix}-airflow-health"
  check_interval_sec = 15
  timeout_sec        = 5

  http_health_check {
    port         = 8082
    request_path = "/airflow/health"
  }
}

resource "google_compute_backend_service" "console" {
  name                  = "${local.name_prefix}-console-backend"
  protocol              = "HTTP"
  port_name             = "console"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  timeout_sec           = 30
  health_checks         = [google_compute_health_check.console.id]
  security_policy       = google_compute_security_policy.public.id

  log_config {
    enable      = var.enable_lb_logging
    sample_rate = var.lb_log_sample_rate
  }

  backend {
    group           = google_compute_instance_group.app.id
    balancing_mode  = "UTILIZATION"
    capacity_scaler = 1.0
  }
}

resource "google_compute_backend_service" "workspace" {
  name                  = "${local.name_prefix}-workspace-backend"
  protocol              = "HTTP"
  port_name             = "workspace"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  timeout_sec           = 30
  health_checks         = [google_compute_health_check.workspace.id]
  security_policy       = google_compute_security_policy.public.id

  log_config {
    enable      = var.enable_lb_logging
    sample_rate = var.lb_log_sample_rate
  }

  backend {
    group           = google_compute_instance_group.app.id
    balancing_mode  = "UTILIZATION"
    capacity_scaler = 1.0
  }
}

resource "google_compute_backend_service" "airflow" {
  name                  = "${local.name_prefix}-airflow-backend"
  protocol              = "HTTP"
  port_name             = "airflow"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  timeout_sec           = 30
  health_checks         = [google_compute_health_check.airflow.id]
  security_policy       = google_compute_security_policy.public.id

  log_config {
    enable      = var.enable_lb_logging
    sample_rate = var.lb_log_sample_rate
  }

  backend {
    group           = google_compute_instance_group.app.id
    balancing_mode  = "UTILIZATION"
    capacity_scaler = 1.0
  }
}

resource "google_compute_url_map" "public" {
  name            = "${local.name_prefix}-url-map"
  default_service = google_compute_backend_service.console.id

  dynamic "host_rule" {
    for_each = local.public_domains_configured ? [1] : []
    content {
      hosts        = [var.public_workspace_domain]
      path_matcher = "workspace"
    }
  }

  host_rule {
    hosts        = ["*"]
    path_matcher = "console"
  }

  path_matcher {
    name            = "console"
    default_service = google_compute_backend_service.console.id

    path_rule {
      paths   = ["/airflow", "/airflow/*"]
      service = google_compute_backend_service.airflow.id
    }
  }

  dynamic "path_matcher" {
    for_each = local.public_domains_configured ? [1] : []
    content {
      name            = "workspace"
      default_service = google_compute_backend_service.workspace.id
    }
  }
}

resource "google_compute_url_map" "http_redirect" {
  count = local.public_https_enabled ? 1 : 0
  name  = "${local.name_prefix}-http-redirect"

  default_url_redirect {
    https_redirect         = true
    redirect_response_code = "MOVED_PERMANENTLY_DEFAULT"
    strip_query            = false
  }
}

resource "google_compute_url_map" "workspace" {
  name            = "${local.name_prefix}-workspace-url-map"
  default_service = google_compute_backend_service.workspace.id
}

resource "google_compute_managed_ssl_certificate" "public" {
  count = local.public_https_enabled ? 1 : 0
  name  = "${local.name_prefix}-public-cert"

  managed {
    domains = local.public_domains
  }
}

resource "google_compute_target_http_proxy" "public" {
  name    = "${local.name_prefix}-http-proxy"
  url_map = google_compute_url_map.public.id
}

resource "google_compute_target_http_proxy" "public_redirect" {
  count   = local.public_https_enabled ? 1 : 0
  name    = "${local.name_prefix}-http-redirect-proxy"
  url_map = google_compute_url_map.http_redirect[0].id
}

resource "google_compute_target_https_proxy" "public" {
  count            = local.public_https_enabled ? 1 : 0
  name             = "${local.name_prefix}-https-proxy"
  url_map          = google_compute_url_map.public.id
  ssl_certificates = [google_compute_managed_ssl_certificate.public[0].id]
}

resource "google_compute_target_http_proxy" "workspace" {
  name    = "${local.name_prefix}-workspace-http-proxy"
  url_map = google_compute_url_map.workspace.id
}

resource "google_compute_global_forwarding_rule" "http" {
  name                  = "${local.name_prefix}-http"
  ip_address            = google_compute_global_address.public.id
  port_range            = "80"
  target                = google_compute_target_http_proxy.public.id
  load_balancing_scheme = "EXTERNAL_MANAGED"
}

resource "google_compute_global_forwarding_rule" "http_redirect" {
  count                 = local.public_https_enabled ? 1 : 0
  name                  = "${local.name_prefix}-http-redirect"
  ip_address            = google_compute_global_address.public_https[0].id
  port_range            = "80"
  target                = google_compute_target_http_proxy.public_redirect[0].id
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
