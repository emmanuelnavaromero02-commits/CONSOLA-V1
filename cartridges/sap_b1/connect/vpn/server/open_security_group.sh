#!/usr/bin/env bash
set -euo pipefail

AWS_REGION="${AWS_REGION:-<AWS_REGION>}"
SG_VPN_ID="${SG_VPN_ID:-<SG_VPN_ID>}"
SG_APP_ID="${SG_APP_ID:-<SG_APP_ID>}"
VPN_INSTANCE_ID="${VPN_INSTANCE_ID:-<VPN_INSTANCE_ID>}"            # the bastion (aws_instance.vpn)
PRIVATE_ROUTE_TABLE_ID="${PRIVATE_ROUTE_TABLE_ID:-<PRIVATE_ROUTE_TABLE_ID>}"
CUSTOMER_PUBLIC_IP="${CUSTOMER_PUBLIC_IP:-<CUSTOMER_PUBLIC_IP>}"   # the egress IP of the customer's Windows server
WG_LISTEN_PORT="${WG_LISTEN_PORT:-<WG_LISTEN_PORT>}"
TENANT_SQL_PORT="${TENANT_SQL_PORT:-<TENANT_SQL_PORT>}"
WG_SUBNET_CIDR="${WG_SUBNET_CIDR:-<WG_SUBNET_CIDR>}"               # the tunnel subnet, e.g. the /24 around WG_SERVER_TUNNEL_IP

die() { echo "error: $*" >&2; exit 1; }
for name in AWS_REGION SG_VPN_ID SG_APP_ID VPN_INSTANCE_ID PRIVATE_ROUTE_TABLE_ID CUSTOMER_PUBLIC_IP WG_LISTEN_PORT TENANT_SQL_PORT WG_SUBNET_CIDR; do
    [[ "${!name}" == *"<"* || -z "${!name}" ]] && die "$name is still a placeholder"
done
[[ "$CUSTOMER_PUBLIC_IP" == */* ]] && die "CUSTOMER_PUBLIC_IP must be a single address; the /32 is added here"
command -v aws >/dev/null 2>&1 || die "aws CLI not found"

export AWS_REGION
UDP_PERMISSION="IpProtocol=udp,FromPort=${WG_LISTEN_PORT},ToPort=${WG_LISTEN_PORT},IpRanges=[{CidrIp=${CUSTOMER_PUBLIC_IP}/32,Description=sap_b1 customer WireGuard}]"
TCP_PERMISSION="IpProtocol=tcp,FromPort=${TENANT_SQL_PORT},ToPort=${TENANT_SQL_PORT},UserIdGroupPairs=[{GroupId=${SG_APP_ID},Description=sap-b1 container to customer tunnel}]"

if [[ "${1:-}" == "--revoke" ]]; then
    aws ec2 revoke-security-group-ingress --group-id "$SG_VPN_ID" --ip-permissions "$UDP_PERMISSION" || echo "UDP rule was not present"
    aws ec2 revoke-security-group-ingress --group-id "$SG_VPN_ID" --ip-permissions "$TCP_PERMISSION" || echo "TCP rule was not present"
    aws ec2 delete-route --route-table-id "$PRIVATE_ROUTE_TABLE_ID" --destination-cidr-block "$WG_SUBNET_CIDR" || echo "route was not present"
    echo "revoked"
    exit 0
fi

echo "1. UDP ${WG_LISTEN_PORT} on ${SG_VPN_ID} from ${CUSTOMER_PUBLIC_IP}/32"
aws ec2 authorize-security-group-ingress --group-id "$SG_VPN_ID" --ip-permissions "$UDP_PERMISSION" \
    || echo "   (already present)"

echo "2. TCP ${TENANT_SQL_PORT} on ${SG_VPN_ID} from ${SG_APP_ID}"
aws ec2 authorize-security-group-ingress --group-id "$SG_VPN_ID" --ip-permissions "$TCP_PERMISSION" \
    || echo "   (already present)"

echo "3. route ${WG_SUBNET_CIDR} -> ${VPN_INSTANCE_ID} in ${PRIVATE_ROUTE_TABLE_ID}"
aws ec2 create-route --route-table-id "$PRIVATE_ROUTE_TABLE_ID" --destination-cidr-block "$WG_SUBNET_CIDR" --instance-id "$VPN_INSTANCE_ID" \
    || echo "   (already present)"

echo
echo "verify:"
echo "  aws ec2 describe-security-groups --group-ids ${SG_VPN_ID} --query 'SecurityGroups[0].IpPermissions'"
echo "  aws ec2 describe-route-tables --route-table-ids ${PRIVATE_ROUTE_TABLE_ID} --query 'RouteTables[0].Routes'"
