#!/usr/bin/env bash
# Install and configure the WireGuard endpoint for the SAP Business One
# customer tunnel on an Ubuntu host (22.04 or later). Idempotent: re-running
# keeps the existing keys, rewrites the config only when it changed and
# restarts the interface only then.
#
# Where it runs: the host that owns the public UDP endpoint. In the current
# AWS layout that is the VPN bastion (public subnet, Elastic IP), not the
# application host, which sits in a private subnet without a public address
# (see ../README.md). The bastion already runs the team VPN (wg-easy) inside
# Docker with its own interface in the container's network namespace; this
# script creates a separate kernel interface on a separate UDP port.
#
# Usage:
#   sudo WG_LISTEN_PORT=... WG_SERVER_TUNNEL_IP=... ./install_wireguard_host.sh --keys-only
#       installs WireGuard and generates OUR keypair; prints only the public key.
#       Run this before the customer has sent anything.
#   sudo WG_LISTEN_PORT=... WG_SERVER_TUNNEL_IP=... CUSTOMER_PEER_PUBLIC_KEY=... \
#        CUSTOMER_PEER_TUNNEL_IP=... TENANT_SQL_PORT=... ./install_wireguard_host.sh
#       writes /etc/wireguard/<WG_IFACE>.conf with the customer's peer, enables
#       forwarding and the firewall rules, enables and starts wg-quick@<WG_IFACE>.
#
# Inputs (environment variables; every default is a placeholder that must be
# replaced, the script refuses to continue while one is left):
#   WG_IFACE                  interface and config name           (default wg0)
#   WG_LISTEN_PORT            UDP port to listen on               (default <WG_LISTEN_PORT>)
#   WG_SERVER_TUNNEL_IP       our address inside the tunnel       (default <WG_SERVER_TUNNEL_IP>)
#   WG_PREFIX_LEN             tunnel subnet prefix length         (default 24)
#   CUSTOMER_PEER_PUBLIC_KEY  the customer's WireGuard public key (default <CUSTOMER_PEER_PUBLIC_KEY>)
#   CUSTOMER_PEER_TUNNEL_IP   the customer's address in the tunnel, no prefix (default <CUSTOMER_PEER_TUNNEL_IP>)
#   TENANT_SQL_PORT           the only TCP port forwarded into the tunnel (default <TENANT_SQL_PORT>)
#
# Nothing here prints a private key. Keys live in /etc/wireguard with mode 600.
set -euo pipefail

WG_IFACE="${WG_IFACE:-wg0}"
WG_LISTEN_PORT="${WG_LISTEN_PORT:-<WG_LISTEN_PORT>}"
WG_SERVER_TUNNEL_IP="${WG_SERVER_TUNNEL_IP:-<WG_SERVER_TUNNEL_IP>}"
WG_PREFIX_LEN="${WG_PREFIX_LEN:-24}"
CUSTOMER_PEER_PUBLIC_KEY="${CUSTOMER_PEER_PUBLIC_KEY:-<CUSTOMER_PEER_PUBLIC_KEY>}"
CUSTOMER_PEER_TUNNEL_IP="${CUSTOMER_PEER_TUNNEL_IP:-<CUSTOMER_PEER_TUNNEL_IP>}"
TENANT_SQL_PORT="${TENANT_SQL_PORT:-<TENANT_SQL_PORT>}"

KEYS_ONLY=0
[[ "${1:-}" == "--keys-only" ]] && KEYS_ONLY=1

WG_DIR=/etc/wireguard
KEY_FILE="$WG_DIR/$WG_IFACE.key"
PUB_FILE="$WG_DIR/$WG_IFACE.pub"
CONF_FILE="$WG_DIR/$WG_IFACE.conf"

die() { echo "error: $*" >&2; exit 1; }

require_filled() {   # refuse to render anything that still carries a <PLACEHOLDER>
    local name
    for name in "$@"; do
        [[ "${!name}" == *"<"* || -z "${!name}" ]] && die "$name is not set (still a placeholder)"
    done
    return 0
}

[[ "$(id -u)" -eq 0 ]] || die "run as root (sudo)"
[[ "$WG_IFACE" =~ ^[A-Za-z0-9_-]{1,15}$ ]] || die "WG_IFACE must be a short interface name"
require_filled WG_LISTEN_PORT WG_SERVER_TUNNEL_IP
[[ "$WG_LISTEN_PORT" =~ ^[0-9]{1,5}$ ]] || die "WG_LISTEN_PORT must be a port number"

# 1. Packages: the kernel module ships with Ubuntu; wireguard-tools brings wg and wg-quick.
if ! command -v wg >/dev/null 2>&1; then
    apt-get update -y
    DEBIAN_FRONTEND=noninteractive apt-get install -y wireguard wireguard-tools
fi

# 2. Our keypair, generated once. umask 077 makes the key file 600 from birth;
#    the public key is derived from it every time so the two never diverge.
umask 077
mkdir -p "$WG_DIR"
chmod 700 "$WG_DIR"
if [[ ! -s "$KEY_FILE" ]]; then
    wg genkey > "$KEY_FILE"
    echo "generated a new private key in $KEY_FILE"
fi
chmod 600 "$KEY_FILE"
wg pubkey < "$KEY_FILE" > "$PUB_FILE"
chmod 644 "$PUB_FILE"
echo "our public key (send this to the customer): $(cat "$PUB_FILE")"

if [[ "$KEYS_ONLY" -eq 1 ]]; then
    echo "keys only: no config written. Re-run without --keys-only once the customer's public key is known."
    exit 0
fi

require_filled CUSTOMER_PEER_PUBLIC_KEY CUSTOMER_PEER_TUNNEL_IP TENANT_SQL_PORT
[[ "$TENANT_SQL_PORT" =~ ^[0-9]{1,5}$ ]] || die "TENANT_SQL_PORT must be a port number"
[[ "$CUSTOMER_PEER_PUBLIC_KEY" =~ ^[A-Za-z0-9+/]{42}=$ ]] || die "CUSTOMER_PEER_PUBLIC_KEY does not look like a WireGuard public key"

# 3. Forwarding at the kernel level: packets arriving from the VPC (or from a
#    Docker bridge when the platform shares this host) must be allowed to
#    leave through the tunnel interface. Persisted so it survives reboots.
cat > /etc/sysctl.d/99-wireguard-sap-b1.conf <<SYSCTL
net.ipv4.ip_forward = 1
SYSCTL
sysctl -q --system >/dev/null

# 4. The interface config, rendered below into a temporary file and installed
#    only when it differs from the one in place.
#
#    PostUp / PostDown (undone symmetrically when the interface goes down):
#      FORWARD 1  only TCP to the customer's tunnel address on the HANA port
#                 (and ICMP echo for diagnostics) may enter the tunnel; the
#                 rule is inserted first so it precedes Docker's own FORWARD
#                 chains on a host that runs Docker (Docker sets the FORWARD
#                 policy to DROP).
#      FORWARD 2  replies that belong to those connections may come back.
#      MASQUERADE the source of forwarded packets becomes our tunnel address:
#                 the only source the customer's AllowedIPs accepts, and the
#                 only address their Windows firewall rule allows.
#    Routing needs no explicit `ip route`: wg-quick installs a route for each
#    peer's AllowedIPs (the customer's /32) through the interface.
#    No Endpoint on the peer: the customer initiates and keeps the tunnel
#    alive (PersistentKeepalive on their side); we never dial out.
RENDERED="$(mktemp)"
trap 'rm -f "$RENDERED"' EXIT
chmod 600 "$RENDERED"
{
    echo "[Interface]"
    echo "Address = ${WG_SERVER_TUNNEL_IP}/${WG_PREFIX_LEN}"
    echo "ListenPort = ${WG_LISTEN_PORT}"
    echo "PrivateKey = $(cat "$KEY_FILE")"
    echo "PostUp = iptables -I FORWARD 1 -o %i -d ${CUSTOMER_PEER_TUNNEL_IP}/32 -p tcp --dport ${TENANT_SQL_PORT} -j ACCEPT"
    echo "PostUp = iptables -I FORWARD 1 -o %i -d ${CUSTOMER_PEER_TUNNEL_IP}/32 -p icmp --icmp-type echo-request -j ACCEPT"
    echo "PostUp = iptables -I FORWARD 1 -i %i -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT"
    echo "PostUp = iptables -t nat -A POSTROUTING -o %i -j MASQUERADE"
    echo "PostDown = iptables -D FORWARD -o %i -d ${CUSTOMER_PEER_TUNNEL_IP}/32 -p tcp --dport ${TENANT_SQL_PORT} -j ACCEPT"
    echo "PostDown = iptables -D FORWARD -o %i -d ${CUSTOMER_PEER_TUNNEL_IP}/32 -p icmp --icmp-type echo-request -j ACCEPT"
    echo "PostDown = iptables -D FORWARD -i %i -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT"
    echo "PostDown = iptables -t nat -D POSTROUTING -o %i -j MASQUERADE"
    echo ""
    echo "[Peer]"
    echo "# The customer's Windows server (or their HANA host). AllowedIPs is their"
    echo "# single tunnel address: nothing else on their side is ever routed here."
    echo "PublicKey = ${CUSTOMER_PEER_PUBLIC_KEY}"
    echo "AllowedIPs = ${CUSTOMER_PEER_TUNNEL_IP}/32"
} > "$RENDERED"

CHANGED=0
if [[ -f "$CONF_FILE" ]] && cmp -s "$CONF_FILE" "$RENDERED"; then
    echo "$CONF_FILE unchanged"
else
    if [[ -f "$CONF_FILE" ]]; then
        cp -p "$CONF_FILE" "$CONF_FILE.$(date +%Y%m%d%H%M%S).bak"   # keeps mode 600
    fi
    cp "$RENDERED" "$CONF_FILE"
    chmod 600 "$CONF_FILE"
    CHANGED=1
    echo "wrote $CONF_FILE"
fi

# 5. systemd unit: enabled so the tunnel comes back after a reboot.
systemctl enable "wg-quick@${WG_IFACE}" >/dev/null
if systemctl is-active --quiet "wg-quick@${WG_IFACE}"; then
    if [[ "$CHANGED" -eq 1 ]]; then
        systemctl restart "wg-quick@${WG_IFACE}"
        echo "restarted wg-quick@${WG_IFACE}"
    fi
else
    systemctl start "wg-quick@${WG_IFACE}"
    echo "started wg-quick@${WG_IFACE}"
fi

# 6. Summary. `wg show` never prints private keys.
echo
wg show "$WG_IFACE"
echo
echo "next: the customer's client config needs our public key above, the endpoint"
echo "      <OMEGA_VPN_PUBLIC_IP>:${WG_LISTEN_PORT} and AllowedIPs = ${WG_SERVER_TUNNEL_IP}/32."
echo "      A handshake appears in 'wg show' once their tunnel is up."
