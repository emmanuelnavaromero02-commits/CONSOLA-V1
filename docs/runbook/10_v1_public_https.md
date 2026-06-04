# v1 Public HTTPS Gate

This runbook is the hard gate before calling a release public v1.

## Required Terraform Inputs

- `public_console_domain`: public console hostname, without scheme.
- `public_workspace_domain`: public workspace hostname, without scheme.
- `alarm_email`: CloudWatch/SNS alarm recipient.
- `route53_zone_id`: set when DNS is in Route53.
- `manual_acm_validation_complete`: keep `false` until manual ACM DNS validation is created and the managed certificate shows `ISSUED`; then set `true` and apply again.
- `public_acm_certificate_arn`: set only when DNS/ACM is managed manually.
- `ssh_allowed_cidrs`: keep empty unless a specific operator CIDR is approved.

## Network Contract

- Public: ALB `80` redirects to `443`; ALB `443` routes only to console `8000` and workspace `8001`.
- Private: Airflow, Superset, MinIO, Postgres, MailHog, MCP and cartridges stay behind VPN/SSM.
- SSH: disabled by default. Prefer `terraform output ssm_app_command` and `terraform output ssm_vpn_command`.
- wg-easy admin: use `terraform output ssm_wg_easy_port_forward_command` unless a temporary explicit `vpn_admin_allowed_cidrs` is approved.

## Release Verification

```bash
PUBLIC_CONSOLE_URL=https://console.example.com \
PUBLIC_WORKSPACE_URL=https://workspace.example.com \
TEST_EMAIL=admin@example.com \
TEST_PASSWORD=... \
E2E_LIVE_LLM=1 \
ANTHROPIC_API_KEY=... \
make verify-v1-public
```

The command must prove:

- `/healthz` and `/readyz` pass over HTTPS.
- HTTP redirects to HTTPS.
- internal ports are not directly reachable.
- public login succeeds and emits `HttpOnly`, `Secure`, `SameSite` session cookies.
- every priority cartridge returns `ok` from live `test_connection`.
- Playwright passes against the public console domain.
- live LLM probes are enabled.

## Backup / Restore / Rollback

```bash
bash /opt/modecissions/infra/terraform/deploy/backup.sh

BACKUP_ID=<id> \
CONFIRM_RESTORE=modecissions \
bash /opt/modecissions/infra/terraform/deploy/restore.sh

bash /opt/modecissions/infra/terraform/deploy/rollback.sh v1.0.0-rc2
```

A v1 release is not closed until backup, restore and rollback have been run on staging and followed by a green smoke.

Production rollback is always tag-based. `DEPLOY_REF` and `IMAGE_TAG` must
point to the same immutable release tag, and the target tag must exist for all
GHCR images before `rollback.sh` is run. Use
`docs/runbook/11_release_stabilization.md` for the detailed freeze and rollback
procedure.

`RESTORE_DELETE_STALE_S3=1` is blocked unless `RESTORE_DELETE_PREFIX` points to
a specific non-backup prefix. Do not run destructive syncs against the bucket
root.

## Partial Datasets

The catalog UI labels datasets as `Parcial` when their descriptions disclose pending extraction or partial semantics. These datasets must not be presented as complete production metrics until their upstream source exists:

- SuccessFactors compensation distribution: `paycompValue` is encrypted and not SQL-aggregable.
- SuccessFactors recruitment funnel/pipeline: candidate stages require `JobApplication`.
- SAP HCM workforce cost: requires `PA0008`.
- SAP HCM org/manager hierarchy: requires relationship/supervisor sources.
- SAP S/4HANA cost center expense: requires account assignment.
- SAP S/4HANA overdue billing/inventory: real paid state or item quantities are not extracted yet.
