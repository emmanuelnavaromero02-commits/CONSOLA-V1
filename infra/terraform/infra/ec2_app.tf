resource "aws_instance" "app" {
  ami                    = data.aws_ami.ubuntu_2204.id
  instance_type          = "m6i.xlarge"
  subnet_id              = aws_subnet.private.id
  vpc_security_group_ids = [aws_security_group.app.id]
  key_name               = var.key_pair_name
  iam_instance_profile   = aws_iam_instance_profile.app.name

  metadata_options {
    http_endpoint = "enabled"
    http_tokens   = "required"
  }

  root_block_device {
    volume_type           = "gp3"
    volume_size           = 150
    delete_on_termination = true
    encrypted             = true
  }

  user_data = templatefile("${path.module}/user_data/app.sh.tpl", {
    github_repo_url    = var.github_repo_url
    deploy_private_key = var.deploy_private_key
  })

  # Repo clone needs outbound internet via NAT GW
  depends_on = [aws_nat_gateway.main]

  tags = {
    Name = "modecissions-app"
  }
}
