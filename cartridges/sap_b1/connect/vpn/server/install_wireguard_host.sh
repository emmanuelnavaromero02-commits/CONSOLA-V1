#!/usr/bin/env bash
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

if ! command -v wg >/dev/null 2>&1; then
    apt-get update -y
    DEBIAN_FRONTEND=noninteractive apt-get install -y wireguard wireguard-tools
fi

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

cat > /etc/sysctl.d/99-wireguard-sap-b1.conf <<SYSCTL
net.ipv4.ip_forward = 1
SYSCTL
sysctl -q --system >/dev/null

RENDERED="$(mktemp)"
trap 'rm -f "$RENDERED"' EXIT
chmod 600 "$RENDERED"
{
    echo "[Interface]"
    echo "Address = ${WG_SERVER_TUNNEL_IP}/${WG_PREFIX_LEN}"
    echo "ListenPort = ${WG_LISTEN_PORT}"
    echo "PrivateKey = $(cat "$KEY_FILE")"
    echo "PostUp = iptables -I FORWARD 1 -i %i -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT"
    echo "PostUp = iptables -I FORWARD 2 -i %i -j DROP"
    echo "PostUp = iptables -I FORWARD 3 -o %i -d ${CUSTOMER_PEER_TUNNEL_IP}/32 -p tcp --dport ${TENANT_SQL_PORT} -j ACCEPT"
    echo "PostUp = iptables -I FORWARD 4 -o %i -d ${CUSTOMER_PEER_TUNNEL_IP}/32 -p icmp --icmp-type echo-request -j ACCEPT"
    echo "PostUp = iptables -I INPUT 1 -i %i -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT"
    echo "PostUp = iptables -I INPUT 2 -i %i -s ${CUSTOMER_PEER_TUNNEL_IP}/32 -p icmp --icmp-type echo-request -j ACCEPT"
    echo "PostUp = iptables -I INPUT 3 -i %i -j DROP"
    echo "PostUp = iptables -t nat -A POSTROUTING -o %i -j MASQUERADE"
    echo "PostDown = iptables -D FORWARD -i %i -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT"
    echo "PostDown = iptables -D FORWARD -i %i -j DROP"
    echo "PostDown = iptables -D FORWARD -o %i -d ${CUSTOMER_PEER_TUNNEL_IP}/32 -p tcp --dport ${TENANT_SQL_PORT} -j ACCEPT"
    echo "PostDown = iptables -D FORWARD -o %i -d ${CUSTOMER_PEER_TUNNEL_IP}/32 -p icmp --icmp-type echo-request -j ACCEPT"
    echo "PostDown = iptables -D INPUT -i %i -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT"
    echo "PostDown = iptables -D INPUT -i %i -s ${CUSTOMER_PEER_TUNNEL_IP}/32 -p icmp --icmp-type echo-request -j ACCEPT"
    echo "PostDown = iptables -D INPUT -i %i -j DROP"
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

echo
wg show "$WG_IFACE"
echo
echo "next: the customer's client config needs our public key above, the endpoint"
echo "      <OMEGA_VPN_PUBLIC_IP>:${WG_LISTEN_PORT} and AllowedIPs = ${WG_SERVER_TUNNEL_IP}/32."
echo "      A handshake appears in 'wg show' once their tunnel is up."
