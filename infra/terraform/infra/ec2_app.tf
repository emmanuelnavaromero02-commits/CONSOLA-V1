resource "aws_instance" "app" {
  ami                    = data.aws_ami.ubuntu_2204.id
  instance_type          = var.app_instance_type
  subnet_id              = aws_subnet.private.id
  vpc_security_group_ids = [aws_security_group.app.id]
  key_name               = var.key_pair_name
  iam_instance_profile   = aws_iam_instance_profile.app.name

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 2
  }

  disable_api_termination = true

  root_block_device {
    volume_type           = "gp3"
    volume_size           = 150
    delete_on_termination = true
    encrypted             = true
  }

  user_data = templatefile("${path.module}/user_data/app.sh.tpl", {
    github_repo_url              = var.github_repo_url
    github_deploy_key_secret_arn = aws_secretsmanager_secret.app["GITHUB_DEPLOY_KEY"].arn
    aws_region                   = var.aws_region
    s3_bucket_name               = aws_s3_bucket.lakehouse.bucket
    deploy_ref                   = var.deploy_ref
    image_tag                    = var.image_tag
    app_env                      = lower(var.app_env)
    cookie_secure                = local.public_https_enabled ? "true" : "false"
    public_console_url           = local.console_public_url
    public_workspace_url         = local.workspace_public_url
    email_provider               = var.email_provider
    smtp_host                    = var.smtp_host
    smtp_port                    = var.smtp_port
    smtp_user                    = var.smtp_user
    smtp_from                    = var.smtp_from
    smtp_from_domain             = var.smtp_from_domain
    smtp_use_tls                 = var.smtp_use_tls ? "true" : "false"
    secret_arns                  = { for key, secret in aws_secretsmanager_secret.app : key => secret.arn }
  })

  depends_on = [
    aws_nat_gateway.main,
    aws_instance.nat,
    aws_route_table.private,
  ]

  tags = {
    Name = "modecissions-app"
  }
}
