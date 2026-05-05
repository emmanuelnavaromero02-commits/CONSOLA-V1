resource "aws_security_group" "vpn" {
  name        = "modecissions-sg-vpn"
  description = "WireGuard VPN bastion: SSH, wg UDP, wg-easy UI"
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "WireGuard"
    from_port   = 51820
    to_port     = 51820
    protocol    = "udp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "SSH"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "wg-easy admin UI"
    from_port   = 51821
    to_port     = 51821
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "modecissions-sg-vpn"
  }
}

resource "aws_security_group" "app" {
  name        = "modecissions-sg-app"
  description = "App EC2: reachable only from VPN SG"
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "All traffic from VPN SG"
    from_port       = 0
    to_port         = 0
    protocol        = "-1"
    security_groups = [aws_security_group.vpn.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "modecissions-sg-app"
  }
}

