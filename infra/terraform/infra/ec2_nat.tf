resource "aws_instance" "nat" {
  count = local.use_nat_instance ? 1 : 0

  ami                         = data.aws_ami.ubuntu_2204.id
  instance_type               = var.nat_instance_type
  subnet_id                   = aws_subnet.public.id
  vpc_security_group_ids      = [aws_security_group.nat[0].id]
  key_name                    = var.key_pair_name
  iam_instance_profile        = aws_iam_instance_profile.nat[0].name
  associate_public_ip_address = true
  source_dest_check           = false

  metadata_options {
    http_endpoint = "enabled"
    http_tokens   = "required"
  }

  user_data = templatefile("${path.module}/user_data/nat.sh.tpl", {})

  tags = {
    Name = "modecissions-nat-instance"
  }
}
