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
  statement {
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.lakehouse.arn,
      "${aws_s3_bucket.lakehouse.arn}/*",
    ]
  }
}

resource "aws_iam_role_policy" "app_s3" {
  name   = "modecissions-app-s3"
  role   = aws_iam_role.app.id
  policy = data.aws_iam_policy_document.app_s3.json
}

data "aws_iam_policy_document" "app_bedrock" {
  statement {
    actions   = ["bedrock:InvokeModel"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "app_bedrock" {
  name   = "modecissions-app-bedrock"
  role   = aws_iam_role.app.id
  policy = data.aws_iam_policy_document.app_bedrock.json
}

resource "aws_iam_instance_profile" "app" {
  name = "modecissions-app-profile"
  role = aws_iam_role.app.name
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
