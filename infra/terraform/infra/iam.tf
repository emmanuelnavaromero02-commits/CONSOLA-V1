data "aws_iam_policy_document" "ec2_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

# ---------- App role ----------

resource "aws_iam_role" "app" {
  name               = "modecissions-app-role"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume_role.json
}

resource "aws_iam_role_policy_attachment" "app_ssm" {
  role       = aws_iam_role.app.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

data "aws_iam_policy_document" "app_s3" {
  # Bucket level. s3:GetBucketVersioning: the refinement publication verifier
  # (refinement/app/publication_verifier_worker.py) fails closed unless the
  # lakehouse bucket reports versioning Status=Enabled, and it runs that check
  # on every refinement /readyz probe, so without it refinement never becomes
  # healthy.
  statement {
    actions = [
      "s3:GetBucketLocation",
      "s3:GetBucketVersioning",
      "s3:ListBucket",
    ]
    resources = [
      aws_s3_bucket.lakehouse.arn,
    ]
  }

  # Object level. s3:GetObjectVersion: staged publication pins every
  # Silver/Gold object by VersionId, and HeadObject/GetObject with a VersionId
  # (omega_lakehouse/s3_storage.py) are authorized by s3:GetObjectVersion, not
  # s3:GetObject. Version-destroying actions (s3:DeleteObjectVersion,
  # s3:PutBucketVersioning) stay out on purpose: the app must not be able to
  # erase a pinned version.
  statement {
    actions = [
      "s3:AbortMultipartUpload",
      "s3:DeleteObject",
      "s3:GetObject",
      "s3:GetObjectVersion",
      "s3:ListMultipartUploadParts",
      "s3:PutObject",
    ]
    resources = [
      "${aws_s3_bucket.lakehouse.arn}/*",
    ]
  }
}

resource "aws_iam_role_policy" "app_s3" {
  name   = "modecissions-app-s3"
  role   = aws_iam_role.app.id
  policy = data.aws_iam_policy_document.app_s3.json
}

locals {
  bedrock_foundation_model_arns = [
    for model_id in var.bedrock_model_ids :
    "arn:aws:bedrock:${var.aws_region}::foundation-model/${model_id}"
  ]
  bedrock_invoke_model_resources = concat(
    local.bedrock_foundation_model_arns,
    var.bedrock_model_resource_arns,
  )
}

data "aws_iam_policy_document" "app_bedrock" {
  statement {
    actions   = ["bedrock:InvokeModel"]
    resources = local.bedrock_invoke_model_resources
  }
}

resource "aws_iam_role_policy" "app_bedrock" {
  name   = "modecissions-app-bedrock"
  role   = aws_iam_role.app.id
  policy = data.aws_iam_policy_document.app_bedrock.json
}

data "aws_iam_policy_document" "app_secretsmanager" {
  statement {
    actions = ["secretsmanager:GetSecretValue"]
    resources = concat(
      [for secret in aws_secretsmanager_secret.app : secret.arn],
      [aws_secretsmanager_secret.ghcr_pull_credentials.arn],
    )
  }
}

resource "aws_iam_role_policy" "app_secretsmanager" {
  name   = "modecissions-app-secretsmanager"
  role   = aws_iam_role.app.id
  policy = data.aws_iam_policy_document.app_secretsmanager.json
}

data "aws_iam_policy_document" "app_ses" {
  count = var.email_provider == "ses" ? 1 : 0

  statement {
    actions = [
      "ses:SendEmail",
      "ses:SendRawEmail",
    ]
    resources = local.ses_domain_enabled ? [aws_ses_domain_identity.sender[0].arn] : ["*"]
  }
}

resource "aws_iam_role_policy" "app_ses" {
  count = var.email_provider == "ses" ? 1 : 0

  name   = "modecissions-app-ses"
  role   = aws_iam_role.app.id
  policy = data.aws_iam_policy_document.app_ses[0].json
}

resource "aws_iam_instance_profile" "app" {
  name = "modecissions-app-profile"
  role = aws_iam_role.app.name
}

# ---------- NAT instance role ----------

resource "aws_iam_role" "nat" {
  count = local.use_nat_instance ? 1 : 0

  name               = "modecissions-nat-role"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume_role.json
}

resource "aws_iam_role_policy_attachment" "nat_ssm" {
  count = local.use_nat_instance ? 1 : 0

  role       = aws_iam_role.nat[0].name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "nat" {
  count = local.use_nat_instance ? 1 : 0

  name = "modecissions-nat-profile"
  role = aws_iam_role.nat[0].name
}

# ---------- VPN role ----------

resource "aws_iam_role" "vpn" {
  name               = "modecissions-vpn-role"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume_role.json
}

resource "aws_iam_role_policy_attachment" "vpn_ssm" {
  role       = aws_iam_role.vpn.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "vpn" {
  name = "modecissions-vpn-profile"
  role = aws_iam_role.vpn.name
}
