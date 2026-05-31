locals {
  public_domains = distinct([
    var.public_console_domain,
    var.public_workspace_domain,
  ])

  manage_public_certificate = var.public_acm_certificate_arn == ""
  manage_public_dns         = var.route53_zone_id != ""
  public_https_enabled      = var.public_acm_certificate_arn != "" || var.route53_zone_id != "" || var.manual_acm_validation_complete

  public_certificate_arn = (
    var.public_acm_certificate_arn != ""
    ? var.public_acm_certificate_arn
    : (
      var.route53_zone_id != ""
      ? aws_acm_certificate_validation.public[0].certificate_arn
      : aws_acm_certificate.public[0].arn
    )
  )
}

resource "aws_acm_certificate" "public" {
  count = local.manage_public_certificate ? 1 : 0

  domain_name               = var.public_console_domain
  subject_alternative_names = [var.public_workspace_domain]
  validation_method         = "DNS"

  lifecycle {
    create_before_destroy = true
  }

  tags = {
    Name = "modecissions-public"
  }
}

resource "aws_route53_record" "public_cert_validation" {
  for_each = local.manage_public_certificate && local.manage_public_dns ? {
    for dvo in aws_acm_certificate.public[0].domain_validation_options :
    dvo.domain_name => {
      name   = dvo.resource_record_name
      record = dvo.resource_record_value
      type   = dvo.resource_record_type
    }
  } : {}

  allow_overwrite = true
  name            = each.value.name
  records         = [each.value.record]
  ttl             = 60
  type            = each.value.type
  zone_id         = var.route53_zone_id
}

resource "aws_acm_certificate_validation" "public" {
  count = local.manage_public_certificate && local.manage_public_dns ? 1 : 0

  certificate_arn         = aws_acm_certificate.public[0].arn
  validation_record_fqdns = [for record in aws_route53_record.public_cert_validation : record.fqdn]
}

resource "aws_lb" "public" {
  name               = "modecissions-public"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = [aws_subnet.public.id, aws_subnet.public_secondary.id]

  enable_deletion_protection = true
  drop_invalid_header_fields = true

  tags = {
    Name = "modecissions-public"
  }
}

resource "aws_lb_target_group" "console" {
  name        = "modecissions-console"
  port        = 8000
  protocol    = "HTTP"
  target_type = "instance"
  vpc_id      = aws_vpc.main.id

  health_check {
    enabled             = true
    path                = "/readyz"
    protocol            = "HTTP"
    matcher             = "200"
    interval            = 15
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }

  tags = {
    Name = "modecissions-console"
  }
}

resource "aws_lb_target_group" "workspace" {
  name        = "modecissions-workspace"
  port        = 8001
  protocol    = "HTTP"
  target_type = "instance"
  vpc_id      = aws_vpc.main.id

  health_check {
    enabled             = true
    path                = "/healthz"
    protocol            = "HTTP"
    matcher             = "200"
    interval            = 15
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }

  tags = {
    Name = "modecissions-workspace"
  }
}

resource "aws_lb_target_group_attachment" "console" {
  target_group_arn = aws_lb_target_group.console.arn
  target_id        = aws_instance.app.id
  port             = 8000
}

resource "aws_lb_target_group_attachment" "workspace" {
  target_group_arn = aws_lb_target_group.workspace.arn
  target_id        = aws_instance.app.id
  port             = 8001
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.public.arn
  port              = "80"
  protocol          = "HTTP"

  default_action {
    type = "redirect"

    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}

resource "aws_lb_listener" "https" {
  count = local.public_https_enabled ? 1 : 0

  load_balancer_arn = aws_lb.public.arn
  port              = "443"
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = local.public_certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.console.arn
  }
}

resource "aws_lb_listener_rule" "workspace_host" {
  count = local.public_https_enabled ? 1 : 0

  listener_arn = aws_lb_listener.https[0].arn
  priority     = 100

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.workspace.arn
  }

  condition {
    host_header {
      values = [var.public_workspace_domain]
    }
  }
}

resource "aws_route53_record" "console_public" {
  count = local.manage_public_dns ? 1 : 0

  name    = var.public_console_domain
  type    = "A"
  zone_id = var.route53_zone_id

  alias {
    evaluate_target_health = true
    name                   = aws_lb.public.dns_name
    zone_id                = aws_lb.public.zone_id
  }
}

resource "aws_route53_record" "workspace_public" {
  count = local.manage_public_dns ? 1 : 0

  name    = var.public_workspace_domain
  type    = "A"
  zone_id = var.route53_zone_id

  alias {
    evaluate_target_health = true
    name                   = aws_lb.public.dns_name
    zone_id                = aws_lb.public.zone_id
  }
}

resource "aws_sns_topic" "public_alarms" {
  name = "modecissions-public-alarms"
}

resource "aws_sns_topic_subscription" "public_alarm_email" {
  topic_arn = aws_sns_topic.public_alarms.arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

resource "aws_cloudwatch_metric_alarm" "alb_console_unhealthy" {
  alarm_name          = "modecissions-console-target-unhealthy"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "UnHealthyHostCount"
  namespace           = "AWS/ApplicationELB"
  period              = 60
  statistic           = "Maximum"
  threshold           = 0
  alarm_description   = "Console ALB target is unhealthy."
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.public_alarms.arn]
  ok_actions          = [aws_sns_topic.public_alarms.arn]

  dimensions = {
    LoadBalancer = aws_lb.public.arn_suffix
    TargetGroup  = aws_lb_target_group.console.arn_suffix
  }
}

resource "aws_cloudwatch_metric_alarm" "alb_workspace_unhealthy" {
  alarm_name          = "modecissions-workspace-target-unhealthy"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "UnHealthyHostCount"
  namespace           = "AWS/ApplicationELB"
  period              = 60
  statistic           = "Maximum"
  threshold           = 0
  alarm_description   = "Workspace ALB target is unhealthy."
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.public_alarms.arn]
  ok_actions          = [aws_sns_topic.public_alarms.arn]

  dimensions = {
    LoadBalancer = aws_lb.public.arn_suffix
    TargetGroup  = aws_lb_target_group.workspace.arn_suffix
  }
}

resource "aws_cloudwatch_metric_alarm" "alb_5xx" {
  alarm_name          = "modecissions-public-alb-5xx"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "HTTPCode_ELB_5XX_Count"
  namespace           = "AWS/ApplicationELB"
  period              = 300
  statistic           = "Sum"
  threshold           = 5
  alarm_description   = "Public ALB is returning elevated 5xx responses."
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.public_alarms.arn]
  ok_actions          = [aws_sns_topic.public_alarms.arn]

  dimensions = {
    LoadBalancer = aws_lb.public.arn_suffix
  }
}

resource "aws_cloudwatch_metric_alarm" "alb_target_response_time" {
  alarm_name          = "modecissions-public-target-response-time"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  metric_name         = "TargetResponseTime"
  namespace           = "AWS/ApplicationELB"
  period              = 60
  statistic           = "Average"
  threshold           = 2
  alarm_description   = "Public ALB target response time is above 2 seconds."
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.public_alarms.arn]
  ok_actions          = [aws_sns_topic.public_alarms.arn]

  dimensions = {
    LoadBalancer = aws_lb.public.arn_suffix
  }
}

resource "aws_cloudwatch_metric_alarm" "app_ec2_status_check" {
  alarm_name          = "modecissions-app-ec2-status-check"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "StatusCheckFailed"
  namespace           = "AWS/EC2"
  period              = 60
  statistic           = "Maximum"
  threshold           = 0
  alarm_description   = "App EC2 status check failed."
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.public_alarms.arn]
  ok_actions          = [aws_sns_topic.public_alarms.arn]

  dimensions = {
    InstanceId = aws_instance.app.id
  }
}

resource "aws_cloudwatch_metric_alarm" "vpn_ec2_status_check" {
  alarm_name          = "modecissions-vpn-ec2-status-check"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "StatusCheckFailed"
  namespace           = "AWS/EC2"
  period              = 60
  statistic           = "Maximum"
  threshold           = 0
  alarm_description   = "VPN EC2 status check failed."
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.public_alarms.arn]
  ok_actions          = [aws_sns_topic.public_alarms.arn]

  dimensions = {
    InstanceId = aws_instance.vpn.id
  }
}
