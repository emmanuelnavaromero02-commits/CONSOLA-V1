resource "aws_instance" "vpn" {
  count = var.enable_vpn ? 1 : 0

  ami                         = data.aws_ami.ubuntu_2204.id
  instance_type               = var.vpn_instance_type
  subnet_id                   = aws_subnet.public.id
  vpc_security_group_ids      = [aws_security_group.vpn[0].id]
  key_name                    = var.key_pair_name
  iam_instance_profile        = aws_iam_instance_profile.vpn.name
  associate_public_ip_address = true

  source_dest_check = false

  metadata_options {
    http_endpoint = "enabled"
    http_tokens   = "required"
  }

  user_data = templatefile("${path.module}/user_data/vpn.sh.tpl", {
    vpn_public_ip     = aws_eip.vpn[0].public_ip
    vpn_password_hash = replace(var.vpn_admin_password_hash, "$", "$$")
  })

  tags = {
    Name = "modecissions-vpn"
  }
}

resource "aws_eip_association" "vpn" {
  count = var.enable_vpn ? 1 : 0

  instance_id   = aws_instance.vpn[0].id
  allocation_id = aws_eip.vpn[0].id
}
