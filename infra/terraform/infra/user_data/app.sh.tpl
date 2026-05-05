#!/bin/bash
set -euxo pipefail
exec > >(tee -a /var/log/userdata.log) 2>&1

echo "[userdata] start: $(date -Iseconds)"

apt-get update -y
DEBIAN_FRONTEND=noninteractive apt-get install -y \
  ca-certificates curl gnupg postgresql-client git
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

cat > /home/ubuntu/.ssh/deploy_key <<'KEYEOF'
${deploy_private_key}
KEYEOF
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
echo "[userdata] repo cloned: $(date -Iseconds)"

date -Iseconds > /opt/modecissions/READY
chown ubuntu:ubuntu /opt/modecissions/READY

echo "[userdata] done: $(date -Iseconds)"
