#!/bin/bash
set -euo pipefail
exec > >(tee -a /var/log/userdata-nat.log) 2>&1

echo "[nat-userdata] start: $(date -Iseconds)"

apt-get update -y
DEBIAN_FRONTEND=noninteractive apt-get install -y \
  ca-certificates curl awscli iptables-persistent

cat > /etc/sysctl.d/99-modecissions-nat.conf <<'SYSCTL'
net.ipv4.ip_forward=1
SYSCTL
sysctl -p /etc/sysctl.d/99-modecissions-nat.conf

DEFAULT_IFACE="$(ip route show default | awk '{print $5; exit}')"
if [[ -z "$DEFAULT_IFACE" ]]; then
  echo "[nat-userdata] could not detect default interface" >&2
  exit 1
fi

iptables -t nat -C POSTROUTING -o "$DEFAULT_IFACE" -j MASQUERADE 2>/dev/null \
  || iptables -t nat -A POSTROUTING -o "$DEFAULT_IFACE" -j MASQUERADE

netfilter-persistent save
systemctl enable netfilter-persistent

echo "[nat-userdata] ready on interface $DEFAULT_IFACE: $(date -Iseconds)"
