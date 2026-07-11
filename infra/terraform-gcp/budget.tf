resource "google_billing_budget" "monthly" {
  provider = google-beta
  count    = var.enable_budget ? 1 : 0

  billing_account = var.billing_account_id
  display_name    = "${local.name_prefix}-monthly-100"

  budget_filter {
    projects = ["projects/${var.project_number}"]
  }

  amount {
    specified_amount {
      currency_code = var.monthly_budget_currency
      units         = tostring(var.monthly_budget_limit_usd)
    }
  }

  threshold_rules {
    threshold_percent = 0.5
    spend_basis       = "CURRENT_SPEND"
  }

  threshold_rules {
    threshold_percent = 0.8
    spend_basis       = "CURRENT_SPEND"
  }

  threshold_rules {
    threshold_percent = 1.0
    spend_basis       = "CURRENT_SPEND"
  }

  depends_on = [google_project_service.required]
}
