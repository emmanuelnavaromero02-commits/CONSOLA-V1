#!/usr/bin/env bash
# AWS side of the customer tunnel, with the aws CLI. Three changes, each
# scoped as narrowly as the layout allows (see ../README.md):
#
#   1. Security group of the VPN bastion: ingress UDP <WG_LISTEN_PORT> from
#      the customer's public IP ONLY (/32). Never a wider range.
#   2. Security group of the VPN bastion: ingress TCP <TENANT_SQL_PORT> from
#      the application host's security group, because the sap-b1 container
#      reaches the tunnel through the bastion (the application host has no
#      public address of its own).
#   3. Private route table: <WG_SUBNET_CIDR> via the bastion instance, so
#      packets from the private subnet to the tunnel addresses leave through
#      the bastion instead of the NAT gateway. The bastion already has
#      source_dest_check disabled (infra/terraform/infra/ec2_vpn.tf).
#
# Step 1 needs the customer's public IP; steps 2 and 3 do not.
# Move these three changes into Terraform (security_groups.tf, vpc.tf) once
# the pilot is confirmed, so they do not stay as drift.
#
# Usage:  fill the placeholders (environment or edit), then
#   ./open_security_group.sh            # apply
#   ./open_security_group.sh --revoke   # undo the three changes
set -euo pipefail

AWS_REGION="${AWS_REGION:-<AWS_REGION>}"
SG_VPN_ID="${SG_VPN_ID:-<SG_VPN_ID>}"                              # modecissions-sg-vpn
SG_APP_ID="${SG_APP_ID:-<SG_APP_ID>}"                              # modecissions-sg-app
VPN_INSTANCE_ID="${VPN_INSTANCE_ID:-<VPN_INSTANCE_ID>}"            # the bastion (aws_instance.vpn)
PRIVATE_ROUTE_TABLE_ID="${PRIVATE_ROUTE_TABLE_ID:-<PRIVATE_ROUTE_TABLE_ID>}"   # modecissions-rt-private
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

# authorize-* fails with InvalidPermission.Duplicate when the rule exists; that
# is the idempotent case, not an error.
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
