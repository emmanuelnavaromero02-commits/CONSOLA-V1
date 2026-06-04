data "aws_caller_identity" "current" {}

locals {
  github_actions_deploy_subject = "repo:${var.github_actions_deploy_repo}:environment:${var.github_actions_deploy_environment}"
}

resource "aws_iam_openid_connect_provider" "github_actions" {
  count = var.enable_github_actions_deploy_role ? 1 : 0

  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"]
}

data "aws_iam_policy_document" "github_actions_deploy_assume" {
  count = var.enable_github_actions_deploy_role ? 1 : 0

  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github_actions[0].arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = [local.github_actions_deploy_subject]
    }
  }
}

resource "aws_iam_role" "github_actions_deploy" {
  count = var.enable_github_actions_deploy_role ? 1 : 0

  name               = "modecissions-github-actions-deploy"
  assume_role_policy = data.aws_iam_policy_document.github_actions_deploy_assume[0].json
}

data "aws_iam_policy_document" "github_actions_deploy" {
  count = var.enable_github_actions_deploy_role ? 1 : 0

  statement {
    sid = "SendDeployCommandToAppInstance"
    actions = [
      "ssm:SendCommand",
    ]
    resources = [
      "arn:aws:ec2:${var.aws_region}:${data.aws_caller_identity.current.account_id}:instance/${aws_instance.app.id}",
      "arn:aws:ssm:${var.aws_region}::document/AWS-RunShellScript",
    ]
  }

  statement {
    sid = "ReadDeployCommandStatus"
    actions = [
      "ssm:GetCommandInvocation",
      "ssm:ListCommandInvocations",
    ]
    resources = ["*"]
  }

  statement {
    sid = "ResolveAppInstance"
    actions = [
      "ec2:DescribeInstances",
      "ec2:DescribeInstanceStatus",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "github_actions_deploy" {
  count = var.enable_github_actions_deploy_role ? 1 : 0

  name   = "modecissions-github-actions-ssm-deploy"
  role   = aws_iam_role.github_actions_deploy[0].id
  policy = data.aws_iam_policy_document.github_actions_deploy[0].json
}
