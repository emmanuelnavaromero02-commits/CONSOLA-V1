#!/bin/bash
set -euxo pipefail
exec > >(tee -a /var/log/userdata.log) 2>&1

echo "[userdata] start: $(date -Iseconds)"

apt-get update -y
DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates curl gnupg

install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc

. /etc/os-release
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $VERSION_CODENAME stable" > /etc/apt/sources.list.d/docker.list

apt-get update -y
DEBIAN_FRONTEND=noninteractive apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

systemctl enable docker
systemctl start docker

mkdir -p /opt/wireguard/data

cat > /opt/wireguard/docker-compose.yml <<'COMPOSE'
services:
  wg-easy:
    image: ghcr.io/wg-easy/wg-easy:14
    container_name: wg-easy
    environment:
      - WG_HOST=${vpn_public_ip}
      - PASSWORD_HASH=$$2b$$12$$NgM4ZOCVIZnJHhpdnACRt.08cdQ0ITR79HW/QJ.YwbhdEOFKD9fo2
      # Split tunnel: solo enrutar tráfico hacia la VPC (10.0.0.0/16) y la red
      # WG interna (10.8.0.0/24) por el tunnel. El resto va por el ISP normal
      # del usuario. DNS vacío para no sobreescribir el del cliente.
      - WG_DEFAULT_DNS=
      - WG_ALLOWED_IPS=10.0.0.0/16,10.8.0.0/24
      - WG_PERSISTENT_KEEPALIVE=25
    volumes:
      - /opt/wireguard/data:/etc/wireguard
    ports:
      - "51820:51820/udp"
      - "51821:51821/tcp"
    restart: unless-stopped
    cap_add:
      - NET_ADMIN
      - SYS_MODULE
    sysctls:
      - net.ipv4.ip_forward=1
      - net.ipv4.conf.all.src_valid_mark=1
COMPOSE

cd /opt/wireguard
docker compose up -d

echo "[userdata] wg-easy started: $(date -Iseconds)" > /opt/wireguard/setup.log
echo "[userdata] done: $(date -Iseconds)"
