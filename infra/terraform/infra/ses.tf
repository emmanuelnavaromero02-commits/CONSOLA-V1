locals {
  ses_domain_enabled   = var.ses_sender_domain != ""
  ses_manage_dns       = local.ses_domain_enabled && var.route53_zone_id != ""
  ses_mail_from_domain = local.ses_domain_enabled ? "${var.ses_mail_from_subdomain}.${var.ses_sender_domain}" : ""
}

resource "aws_ses_domain_identity" "sender" {
  count = local.ses_domain_enabled ? 1 : 0

  domain = var.ses_sender_domain
}

resource "aws_ses_domain_dkim" "sender" {
  count = local.ses_domain_enabled ? 1 : 0

  domain = aws_ses_domain_identity.sender[0].domain
}

resource "aws_ses_domain_mail_from" "sender" {
  count = local.ses_domain_enabled ? 1 : 0

  domain           = aws_ses_domain_identity.sender[0].domain
  mail_from_domain = local.ses_mail_from_domain
}

resource "aws_route53_record" "ses_domain_verification" {
  count = local.ses_manage_dns ? 1 : 0

  zone_id = var.route53_zone_id
  name    = "_amazonses.${var.ses_sender_domain}"
  type    = "TXT"
  ttl     = 600
  records = [aws_ses_domain_identity.sender[0].verification_token]
}

resource "aws_route53_record" "ses_dkim" {
  for_each = local.ses_manage_dns ? {
    for token in aws_ses_domain_dkim.sender[0].dkim_tokens :
    token => token
  } : {}

  zone_id = var.route53_zone_id
  name    = "${each.value}._domainkey.${var.ses_sender_domain}"
  type    = "CNAME"
  ttl     = 600
  records = ["${each.value}.dkim.amazonses.com"]
}

resource "aws_route53_record" "ses_mail_from_mx" {
  count = local.ses_manage_dns ? 1 : 0

  zone_id = var.route53_zone_id
  name    = local.ses_mail_from_domain
  type    = "MX"
  ttl     = 600
  records = ["10 feedback-smtp.${var.aws_region}.amazonses.com"]
}

resource "aws_route53_record" "ses_mail_from_spf" {
  count = local.ses_manage_dns ? 1 : 0

  zone_id = var.route53_zone_id
  name    = local.ses_mail_from_domain
  type    = "TXT"
  ttl     = 600
  records = ["v=spf1 include:amazonses.com ~all"]
}
