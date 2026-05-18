#!/usr/bin/env bash
set -Eeuo pipefail

AWS_REGION="${AWS_REGION:-us-east-1}"
TFSTATE_BUCKET="${TFSTATE_BUCKET:-modecissions-tfstate-${AWS_REGION}}"
TFLOCK_TABLE="${TFLOCK_TABLE:-modecissions-tfstate-lock}"

echo "[tf-backend] region=$AWS_REGION bucket=$TFSTATE_BUCKET table=$TFLOCK_TABLE"

if aws s3api head-bucket --bucket "$TFSTATE_BUCKET" >/dev/null 2>&1; then
  echo "[tf-backend] bucket already exists"
else
  if [[ "$AWS_REGION" == "us-east-1" ]]; then
    aws s3api create-bucket --bucket "$TFSTATE_BUCKET" --region "$AWS_REGION"
  else
    aws s3api create-bucket \
      --bucket "$TFSTATE_BUCKET" \
      --region "$AWS_REGION" \
      --create-bucket-configuration LocationConstraint="$AWS_REGION"
  fi
fi

aws s3api put-bucket-versioning \
  --bucket "$TFSTATE_BUCKET" \
  --versioning-configuration Status=Enabled

aws s3api put-bucket-encryption \
  --bucket "$TFSTATE_BUCKET" \
  --server-side-encryption-configuration '{
    "Rules": [{
      "ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}
    }]
  }'

aws s3api put-public-access-block \
  --bucket "$TFSTATE_BUCKET" \
  --public-access-block-configuration '{
    "BlockPublicAcls": true,
    "IgnorePublicAcls": true,
    "BlockPublicPolicy": true,
    "RestrictPublicBuckets": true
  }'

bucket_policy="$(mktemp)"
trap 'rm -f "$bucket_policy"' EXIT
cat > "$bucket_policy" <<JSON
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "DenyInsecureTransport",
      "Effect": "Deny",
      "Principal": "*",
      "Action": "s3:*",
      "Resource": [
        "arn:aws:s3:::$TFSTATE_BUCKET",
        "arn:aws:s3:::$TFSTATE_BUCKET/*"
      ],
      "Condition": {
        "Bool": {"aws:SecureTransport": "false"}
      }
    },
    {
      "Sid": "DenyUnencryptedObjectUploads",
      "Effect": "Deny",
      "Principal": "*",
      "Action": "s3:PutObject",
      "Resource": "arn:aws:s3:::$TFSTATE_BUCKET/*",
      "Condition": {
        "StringNotEquals": {"s3:x-amz-server-side-encryption": "AES256"}
      }
    },
    {
      "Sid": "DenyMissingEncryptionHeader",
      "Effect": "Deny",
      "Principal": "*",
      "Action": "s3:PutObject",
      "Resource": "arn:aws:s3:::$TFSTATE_BUCKET/*",
      "Condition": {
        "Null": {"s3:x-amz-server-side-encryption": "true"}
      }
    }
  ]
}
JSON
aws s3api put-bucket-policy --bucket "$TFSTATE_BUCKET" --policy "file://$bucket_policy"

if aws dynamodb describe-table --table-name "$TFLOCK_TABLE" --region "$AWS_REGION" >/dev/null 2>&1; then
  echo "[tf-backend] DynamoDB lock table already exists"
  billing_mode="$(aws dynamodb describe-table \
    --table-name "$TFLOCK_TABLE" \
    --region "$AWS_REGION" \
    --query 'Table.BillingModeSummary.BillingMode' \
    --output text)"
  if [[ "$billing_mode" != "PAY_PER_REQUEST" ]]; then
    aws dynamodb update-table \
      --table-name "$TFLOCK_TABLE" \
      --region "$AWS_REGION" \
      --billing-mode PAY_PER_REQUEST >/dev/null
  fi
else
  aws dynamodb create-table \
    --table-name "$TFLOCK_TABLE" \
    --region "$AWS_REGION" \
    --attribute-definitions AttributeName=LockID,AttributeType=S \
    --key-schema AttributeName=LockID,KeyType=HASH \
    --billing-mode PAY_PER_REQUEST
  aws dynamodb wait table-exists --table-name "$TFLOCK_TABLE" --region "$AWS_REGION"
fi

echo "[tf-backend] backend ready"
