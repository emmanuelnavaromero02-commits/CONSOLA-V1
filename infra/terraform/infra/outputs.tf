output "vpc_id" {
  description = "VPC id"
  value       = aws_vpc.main.id
}

output "ec2_vpn_public_ip" {
  description = "Elastic IP attached to the VPN bastion"
  value       = var.enable_vpn ? aws_eip.vpn[0].public_ip : null
}

output "ec2_vpn_instance_id" {
  description = "Instance id of the VPN bastion"
  value       = var.enable_vpn ? aws_instance.vpn[0].id : null
}

output "ec2_app_private_ip" {
  description = "Private IP of the App EC2"
  value       = aws_instance.app.private_ip
}

output "ec2_app_instance_id" {
  description = "Instance id of the App EC2"
  value       = aws_instance.app.id
}

output "s3_bucket_name" {
  description = "Lakehouse S3 bucket name"
  value       = aws_s3_bucket.lakehouse.bucket
}

output "ssh_vpn_command" {
  description = "Legacy SSH command to reach the VPN bastion, only usable when ssh_allowed_cidrs is configured"
  value       = var.enable_vpn ? "ssh -i modecissions-key.pem ubuntu@${aws_eip.vpn[0].public_ip}" : null
}

output "ssh_app_command" {
  description = "SSH command to reach the App EC2 (only over the VPN)"
  value       = "ssh -i modecissions-key.pem ubuntu@${aws_instance.app.private_ip}"
}

output "wg_easy_url" {
  description = "wg-easy admin UI URL, only reachable when vpn_admin_allowed_cidrs is configured"
  value       = var.enable_vpn ? "http://${aws_eip.vpn[0].public_ip}:51821" : null
}

output "ssm_wg_easy_port_forward_command" {
  description = "SSM port-forward command for wg-easy admin UI without opening TCP 51821 publicly"
  value       = var.enable_vpn ? "aws ssm start-session --target ${aws_instance.vpn[0].id} --document-name AWS-StartPortForwardingSession --parameters '{\"portNumber\":[\"51821\"],\"localPortNumber\":[\"51821\"]}' --region ${var.aws_region}" : null
}

output "public_console_url" {
  description = "Public console URL. Uses ALB HTTP in technical mode and HTTPS domain in production mode."
  value       = local.console_public_url
}

output "public_workspace_url" {
  description = "Public workspace URL. Uses ALB HTTP :8081 in technical mode and HTTPS domain in production mode."
  value       = local.workspace_public_url
}

output "alb_dns_name" {
  description = "Public ALB DNS name"
  value       = aws_lb.public.dns_name
}

output "managed_acm_certificate_arn" {
  description = "ACM certificate ARN managed by this stack, or the supplied public_acm_certificate_arn when external"
  value       = local.manage_public_certificate ? aws_acm_certificate.public[0].arn : var.public_acm_certificate_arn
}

output "public_https_enabled" {
  description = "True when Terraform has enough certificate material to create the HTTPS listener"
  value       = local.public_https_enabled
}

output "ssm_app_command" {
  description = "SSM command to reach the App EC2 without public SSH"
  value       = "aws ssm start-session --target ${aws_instance.app.id} --region ${var.aws_region}"
}

output "ssm_vpn_command" {
  description = "SSM command to reach the VPN EC2 without public SSH"
  value       = var.enable_vpn ? "aws ssm start-session --target ${aws_instance.vpn[0].id} --region ${var.aws_region}" : null
}

output "acm_validation_records" {
  description = "Manual ACM DNS validation records when route53_zone_id is empty and public_acm_certificate_arn is not supplied"
  value = (
    local.manage_public_certificate
    ? [
      for dvo in aws_acm_certificate.public[0].domain_validation_options : {
        domain = dvo.domain_name
        name   = dvo.resource_record_name
        type   = dvo.resource_record_type
        value  = dvo.resource_record_value
      }
    ]
    : []
  )
}

output "egress_mode" {
  description = "Private subnet outbound internet mode."
  value       = var.egress_mode
}

output "nat_instance_id" {
  description = "NAT instance id when egress_mode is nat_instance."
  value       = local.use_nat_instance ? aws_instance.nat[0].id : null
}

output "monthly_budget_name" {
  description = "AWS Budgets guardrail name."
  value       = var.enable_monthly_budget ? aws_budgets_budget.monthly_guardrail[0].name : null
}

output "ses_domain_verification_record" {
  description = "Manual Google DNS TXT record required to verify the SES sender domain."
  value = local.ses_domain_enabled ? {
    name  = "_amazonses.${var.ses_sender_domain}"
    type  = "TXT"
    value = aws_ses_domain_identity.sender[0].verification_token
  } : null
}

output "ses_dkim_records" {
  description = "Manual Google DNS CNAME records required for SES DKIM."
  value = local.ses_domain_enabled ? [
    for token in aws_ses_domain_dkim.sender[0].dkim_tokens : {
      name  = "${token}._domainkey.${var.ses_sender_domain}"
      type  = "CNAME"
      value = "${token}.dkim.amazonses.com"
    }
  ] : []
}

output "ses_mail_from_records" {
  description = "Manual Google DNS records required for SES custom MAIL FROM."
  value = local.ses_domain_enabled ? [
    {
      name  = local.ses_mail_from_domain
      type  = "MX"
      value = "10 feedback-smtp.${var.aws_region}.amazonses.com"
    },
    {
      name  = local.ses_mail_from_domain
      type  = "TXT"
      value = "v=spf1 include:amazonses.com ~all"
    },
  ] : []
}
