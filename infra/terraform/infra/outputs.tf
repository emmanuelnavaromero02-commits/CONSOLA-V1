output "vpc_id" {
  description = "VPC id"
  value       = aws_vpc.main.id
}

output "ec2_vpn_public_ip" {
  description = "Elastic IP attached to the VPN bastion"
  value       = aws_eip.vpn.public_ip
}

output "ec2_vpn_instance_id" {
  description = "Instance id of the VPN bastion"
  value       = aws_instance.vpn.id
}

output "ec2_app_private_ip" {
  description = "Private IP of the App EC2"
  value       = aws_instance.app.private_ip
}

output "ec2_app_instance_id" {
  description = "Instance id of the App EC2"
  value       = aws_instance.app.id
}

output "s3_bucket_name" {
  description = "Lakehouse S3 bucket name"
  value       = aws_s3_bucket.lakehouse.bucket
}

output "ssh_vpn_command" {
  description = "SSH command to reach the VPN bastion"
  value       = "ssh -i modecissions-key.pem ubuntu@${aws_eip.vpn.public_ip}"
}

output "ssh_app_command" {
  description = "SSH command to reach the App EC2 (only over the VPN)"
  value       = "ssh -i modecissions-key.pem ubuntu@${aws_instance.app.private_ip}"
}

output "wg_easy_url" {
  description = "wg-easy admin UI URL"
  value       = "http://${aws_eip.vpn.public_ip}:51821"
}
