#!/bin/bash
set -euo pipefail
exec > >(tee -a /var/log/userdata.log) 2>&1

echo "[userdata] start: $(date -Iseconds)"

apt-get update -y
DEBIAN_FRONTEND=noninteractive apt-get install -y \
  ca-certificates curl gnupg postgresql-client git awscli
echo "[userdata] base packages installed: $(date -Iseconds)"

install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc

. /etc/os-release
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $VERSION_CODENAME stable" > /etc/apt/sources.list.d/docker.list

apt-get update -y
DEBIAN_FRONTEND=noninteractive apt-get install -y \
  docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
echo "[userdata] docker installed: $(date -Iseconds)"

systemctl enable docker
systemctl start docker
usermod -aG docker ubuntu
echo "[userdata] docker ready: $(date -Iseconds)"

mkdir -p /home/ubuntu/.ssh
chmod 700 /home/ubuntu/.ssh

aws --region ${aws_region} secretsmanager get-secret-value \
  --secret-id ${github_deploy_key_secret_arn} \
  --query SecretString \
  --output text > /home/ubuntu/.ssh/deploy_key
chmod 600 /home/ubuntu/.ssh/deploy_key

cat > /home/ubuntu/.ssh/config <<'SSHCONF'
Host github.com
  IdentityFile /home/ubuntu/.ssh/deploy_key
  StrictHostKeyChecking no
  UserKnownHostsFile /dev/null
SSHCONF
chmod 600 /home/ubuntu/.ssh/config

chown -R ubuntu:ubuntu /home/ubuntu/.ssh
echo "[userdata] deploy key configured: $(date -Iseconds)"

mkdir -p /opt/modecissions
chown ubuntu:ubuntu /opt/modecissions
sudo -u ubuntu git clone ${github_repo_url} /opt/modecissions
sudo -u ubuntu git -C /opt/modecissions fetch --tags --force --prune origin
sudo -u ubuntu git -C /opt/modecissions checkout --detach ${deploy_ref}
echo "[userdata] repo cloned and checked out at ${deploy_ref}: $(date -Iseconds)"

mkdir -p /etc/modecissions
IMDS_TOKEN="$(curl -fsS -X PUT http://169.254.169.254/latest/api/token \
  -H "X-aws-ec2-metadata-token-ttl-seconds: 21600")"
APP_PRIVATE_IP="$(curl -fsS -H "X-aws-ec2-metadata-token: $IMDS_TOKEN" \
  http://169.254.169.254/latest/meta-data/local-ipv4)"

cat > /etc/modecissions/aws-entrypoint.env <<ENVEOF
AWS_REGION=${aws_region}
S3_BUCKET_NAME=${s3_bucket_name}
AIRFLOW_ADMIN_USER=admin
SUPERSET_ADMIN_USER=admin
GHCR_OWNER=emmanuelnavaromero02-commits
IMAGE_TAG=${image_tag}
DEPLOY_REF=${deploy_ref}
APP_ENV=${app_env}
COOKIE_SECURE=${cookie_secure}
CONSOLE_URL=${public_console_url}
WORKSPACE_PUBLIC_URL=${public_workspace_url}
APP_BASE_URL=${public_console_url}
AIRFLOW_PUBLIC_URL=${public_console_url}/airflow
SUPERSET_PUBLIC_URL=http://$APP_PRIVATE_IP:8088
ALLOWED_ORIGINS=${public_console_url},${public_workspace_url}
EMAIL_PROVIDER=${email_provider}
SMTP_HOST=${smtp_host}
SMTP_PORT=${smtp_port}
SMTP_USER=${smtp_user}
SMTP_FROM=${smtp_from}
SMTP_FROM_DOMAIN=${smtp_from_domain}
SMTP_USE_TLS=${smtp_use_tls}
MODECISSIONS_ENV_FILE=/opt/modecissions/infra/terraform/deploy/.env
%{ for name, arn in secret_arns ~}
MODECISSIONS_SECRET_${name}_ARN=${arn}
%{ endfor ~}
ENVEOF
chmod 600 /etc/modecissions/aws-entrypoint.env

bash /opt/modecissions/scripts/aws-entrypoint.sh
echo "[userdata] secrets injected: $(date -Iseconds)"

if [[ -f /opt/modecissions/infra/terraform/files/omega-backup-aws.cron ]]; then
  install -m 0644 -o root -g root \
    /opt/modecissions/infra/terraform/files/omega-backup-aws.cron /etc/cron.d/omega-backup
  touch /var/log/omega-backup.log
  chmod 0640 /var/log/omega-backup.log
  echo "[userdata] installed /etc/cron.d/omega-backup"
fi

date -Iseconds > /opt/modecissions/READY
chown ubuntu:ubuntu /opt/modecissions/READY

echo "[userdata] done: $(date -Iseconds)"
