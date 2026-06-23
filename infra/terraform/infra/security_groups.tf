resource "aws_security_group" "vpn" {
  count = var.enable_vpn ? 1 : 0

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

  dynamic "ingress" {
    for_each = length(var.ssh_allowed_cidrs) > 0 ? [1] : []
    content {
      description = "SSH restricted to operator CIDRs"
      from_port   = 22
      to_port     = 22
      protocol    = "tcp"
      cidr_blocks = var.ssh_allowed_cidrs
    }
  }

  dynamic "ingress" {
    for_each = length(var.vpn_admin_allowed_cidrs) > 0 ? [1] : []
    content {
      description = "wg-easy admin UI"
      from_port   = 51821
      to_port     = 51821
      protocol    = "tcp"
      cidr_blocks = var.vpn_admin_allowed_cidrs
    }
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

resource "aws_security_group" "nat" {
  count = local.use_nat_instance ? 1 : 0

  name        = "modecissions-sg-nat-instance"
  description = "Low-cost NAT instance: outbound egress for private subnets"
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "Private subnet egress through NAT instance"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = [aws_vpc.main.cidr_block]
  }

  egress {
    description = "Outbound internet"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "modecissions-sg-nat-instance"
  }
}

resource "aws_security_group" "alb" {
  name        = "modecissions-sg-public-alb"
  description = "Public ALB: HTTPS for console/workspace only"
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "HTTP console"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "HTTPS"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description = "Console target in VPC"
    from_port   = 8000
    to_port     = 8000
    protocol    = "tcp"
    cidr_blocks = [aws_vpc.main.cidr_block]
  }

  egress {
    description = "Workspace target in VPC"
    from_port   = 8001
    to_port     = 8001
    protocol    = "tcp"
    cidr_blocks = [aws_vpc.main.cidr_block]
  }

  egress {
    description = "Airflow target in VPC"
    from_port   = 8082
    to_port     = 8082
    protocol    = "tcp"
    cidr_blocks = [aws_vpc.main.cidr_block]
  }

  tags = {
    Name = "modecissions-sg-public-alb"
  }
}

resource "aws_security_group" "app" {
  name        = "modecissions-sg-app"
  description = "App EC2: internal services from VPN, public console/workspace from ALB"
  vpc_id      = aws_vpc.main.id

  dynamic "ingress" {
    for_each = var.enable_vpn ? [1] : []
    content {
      description     = "All traffic from VPN SG"
      from_port       = 0
      to_port         = 0
      protocol        = "-1"
      security_groups = [aws_security_group.vpn[0].id]
    }
  }

  ingress {
    description     = "Console from public ALB"
    from_port       = 8000
    to_port         = 8000
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  ingress {
    description     = "Workspace from public ALB"
    from_port       = 8001
    to_port         = 8001
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  ingress {
    description     = "Airflow from public ALB"
    from_port       = 8082
    to_port         = 8082
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
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
