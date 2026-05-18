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
terraform plan
terraform apply
```

The full apply expects the runtime secrets to already have versions in
AWS Secrets Manager. Do not pass deploy keys, API keys, or database
passwords as Terraform variables; they would land in state.

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
