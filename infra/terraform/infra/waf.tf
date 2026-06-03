locals {
  public_alb_waf_common_rule_count = lower(var.public_alb_waf_common_rule_action) == "count"
}

resource "aws_wafv2_web_acl" "public_alb" {
  count = var.enable_public_alb_waf ? 1 : 0

  name        = "modecissions-public-alb-waf"
  description = "Low-cost baseline WAF for the public ALB: rate limiting, AWS IP reputation, known bad inputs, and CommonRuleSet observe/block."
  scope       = "REGIONAL"

  default_action {
    allow {}
  }

  rule {
    name     = "RateLimitPerIp"
    priority = 0

    action {
      block {}
    }

    statement {
      rate_based_statement {
        aggregate_key_type = "IP"
        limit              = var.public_alb_waf_rate_limit
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "ModecissionsRateLimitPerIp"
      sampled_requests_enabled   = true
    }
  }

  rule {
    name     = "AWSManagedRulesAmazonIpReputationList"
    priority = 10

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesAmazonIpReputationList"
        vendor_name = "AWS"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "ModecissionsAmazonIpReputation"
      sampled_requests_enabled   = true
    }
  }

  rule {
    name     = "AWSManagedRulesKnownBadInputsRuleSet"
    priority = 20

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesKnownBadInputsRuleSet"
        vendor_name = "AWS"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "ModecissionsKnownBadInputs"
      sampled_requests_enabled   = true
    }
  }

  rule {
    name     = "AWSManagedRulesCommonRuleSet"
    priority = 30

    override_action {
      dynamic "count" {
        for_each = local.public_alb_waf_common_rule_count ? [1] : []
        content {}
      }

      dynamic "none" {
        for_each = local.public_alb_waf_common_rule_count ? [] : [1]
        content {}
      }
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesCommonRuleSet"
        vendor_name = "AWS"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "ModecissionsCommonRuleSet"
      sampled_requests_enabled   = true
    }
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "ModecissionsPublicAlbWaf"
    sampled_requests_enabled   = true
  }

  tags = {
    Name = "modecissions-public-alb-waf"
  }
}

resource "aws_wafv2_web_acl_association" "public_alb" {
  count = var.enable_public_alb_waf ? 1 : 0

  resource_arn = aws_lb.public.arn
  web_acl_arn  = aws_wafv2_web_acl.public_alb[0].arn
}
