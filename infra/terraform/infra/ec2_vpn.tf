resource "aws_instance" "vpn" {
  ami                         = data.aws_ami.ubuntu_2204.id
  instance_type               = "t3.nano"
  subnet_id                   = aws_subnet.public.id
  vpc_security_group_ids      = [aws_security_group.vpn.id]
  key_name                    = var.key_pair_name
  iam_instance_profile        = aws_iam_instance_profile.vpn.name
  associate_public_ip_address = true

  source_dest_check = false

  metadata_options {
    http_endpoint = "enabled"
    http_tokens   = "required"
  }

  user_data = templatefile("${path.module}/user_data/vpn.sh.tpl", {
    vpn_public_ip = aws_eip.vpn.public_ip
  })

  tags = {
    Name = "modecissions-vpn"
  }
}

resource "aws_eip_association" "vpn" {
  instance_id   = aws_instance.vpn.id
  allocation_id = aws_eip.vpn.id
}
