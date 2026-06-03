output "vpc_id" {
  description = "VPC id"
  value       = aws_vpc.main.id
}

output "ec2_vpn_public_ip" {
  description = "Elastic IP attached to the VPN bastion"
  value       = aws_eip.vpn.public_ip
}

output "ec2_vpn_instance_id" {
  description = "Instance id of the VPN bastion"
  value       = aws_instance.vpn.id
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
  value       = "ssh -i modecissions-key.pem ubuntu@${aws_eip.vpn.public_ip}"
}

output "ssh_app_command" {
  description = "SSH command to reach the App EC2 (only over the VPN)"
  value       = "ssh -i modecissions-key.pem ubuntu@${aws_instance.app.private_ip}"
}

output "wg_easy_url" {
  description = "wg-easy admin UI URL, only reachable when vpn_admin_allowed_cidrs is configured"
  value       = "http://${aws_eip.vpn.public_ip}:51821"
}

output "ssm_wg_easy_port_forward_command" {
  description = "SSM port-forward command for wg-easy admin UI without opening TCP 51821 publicly"
  value       = "aws ssm start-session --target ${aws_instance.vpn.id} --document-name AWS-StartPortForwardingSession --parameters '{\"portNumber\":[\"51821\"],\"localPortNumber\":[\"51821\"]}' --region ${var.aws_region}"
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
  value       = "aws ssm start-session --target ${aws_instance.vpn.id} --region ${var.aws_region}"
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
