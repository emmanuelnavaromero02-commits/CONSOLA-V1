# MODecissions Terraform Infra

This stack uses a remote S3 backend with DynamoDB locking. Do not keep
`terraform.tfstate` locally or commit `.tfvars` files with secrets.

## First-Time Backend Bootstrap

From the repository root:

```bash
export AWS_REGION=us-east-1
bash scripts/bootstrap_tf_backend.sh
cd infra/terraform/infra
terraform init
terraform apply -target=aws_secretsmanager_secret.app
# Load required Secrets Manager values, including modecissions/github_deploy_key.
cp terraform.tfvars.example terraform.tfvars
# Edit public domains, alarm_email, deploy_ref/image_tag and optional Route53/ACM values.
terraform plan
terraform apply
```

The full apply expects the runtime secrets to already have versions in
AWS Secrets Manager. Do not pass deploy keys, API keys, or database
passwords as Terraform variables; they would land in state.

Public v1 HTTPS is served by an ALB. Provide `public_console_domain`,
`public_workspace_domain` and either:

- `route53_zone_id`, so Terraform creates ACM validation and A-alias DNS; or
- manual DNS validation using `terraform output acm_validation_records`; after
  ACM shows the managed certificate as `ISSUED`, set
  `manual_acm_validation_complete = true` and apply again; or
- `public_acm_certificate_arn`, using a certificate already issued in the
  same region as the ALB.

SSH is disabled by default (`ssh_allowed_cidrs = []`). Use the SSM outputs
for instance access unless an explicit operator CIDR is approved.

## Low-Cost Staging Mode

For AWS validation without a public domain, keep `app_env = "staging"` and
leave `public_console_domain` / `public_workspace_domain` empty. The stack then
serves technical HTTP URLs from the ALB.

To reduce always-on networking cost in staging, set:

```hcl
egress_mode = "nat_instance"
enable_vpn  = false
```

`nat_instance` replaces the managed NAT Gateway with a single EC2 NAT instance.
It is cheaper but not highly available. It is intended for staging/demo
validation, not enterprise production. `enable_vpn = false` keeps operator
access through SSM and public app access through the ALB.

Keep `enable_monthly_budget = true` and set `monthly_budget_limit_usd` to the
amount where you want AWS Budgets emails to start warning the operator.

## Transactional Email

The application already sends invitations, password reset links and draft
delivery through SMTP. Keep MailHog for local/staging tests until a real sender
domain exists.

For Amazon SES API after domain verification and sandbox removal:

```hcl
email_provider    = "ses"
ses_sender_domain = "example.com"
smtp_from        = "no-reply@example.com"
smtp_from_domain = "example.com"
```

If DNS is managed in Route53, Terraform can create SES verification, DKIM and
MAIL FROM records. If DNS is managed outside AWS, use the
`ses_domain_verification_record`, `ses_dkim_records` and
`ses_mail_from_records` outputs and copy them into the external DNS provider.

SES still requires production access approval before it can send to arbitrary
recipient addresses. Until then it only sends to verified recipients/domains.

For MailHog or an external SMTP provider, keep `email_provider = "smtp"` and
store the SMTP password in `modecissions/smtp_password`; do not put passwords
in `terraform.tfvars`.

If you already have local state, `terraform init` will ask whether to
migrate it into S3. Answer yes only after confirming the S3 bucket has
versioning and encryption enabled.

## Backend Override

Terraform backend blocks cannot use variables. For another region or
account, bootstrap the target bucket/table and run:

```bash
terraform init \
  -backend-config="bucket=modecissions-tfstate-eu-west-1" \
  -backend-config="region=eu-west-1" \
  -backend-config="dynamodb_table=modecissions-tfstate-lock"
```

## Rotating Backend Location

1. Run `bash scripts/bootstrap_tf_backend.sh` with the new `AWS_REGION`.
2. Run `terraform init -migrate-state` with the new backend config.
3. Confirm `terraform plan` is empty before applying changes.
