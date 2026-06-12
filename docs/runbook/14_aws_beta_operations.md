# AWS Beta Operations

This runbook is for the private beta path while GitHub Actions are disabled for
cost control. Run these commands from a trusted local machine with AWS CLI access
to the application EC2 instance through SSM.

## Golden Path

AWS beta readiness is blocked by the Replicon Gold path:

- `gold_consultor_mensual`
- `gold_pnl_mensual`
- `gold_forecast_mensual`
- Replicon `silver_lineage`
- Intelligence signals
- Control Room items

HubSpot is optional for this AWS beta unless `OMEGA_BETA_REQUIRE_HUBSPOT=1` is
set by an operator for a specific release candidate.

## Deploy Guard

Before a deploy, record the current immutable refs:

```bash
export DEPLOY_REF_OLD=<previous-release-tag>
export IMAGE_TAG_OLD=<previous-release-tag>
```

The AWS compose file must mount the host version file:

```text
/opt/modecissions/VERSION:/app/VERSION:ro
```

`make beta-smoke-aws` verifies by SSM that `/opt/modecissions/VERSION` exists on
the EC2 host and that `/healthz` exposes the same version through the public ALB
and through an internal EC2 curl.

## Seed Replicon Gold

The AWS seed is intentionally scoped and mutating. Tenant and workspace are
required so repeated runs cannot touch unrelated data:

```bash
AWS_REGION=us-east-1 \
AWS_APP_INSTANCE_ID=i-xxxxxxxxxxxxxxxxx \
OMEGA_SEED_TENANT_ID=<tenant-uuid> \
OMEGA_SEED_WORKSPACE_ID=<workspace-uuid> \
make seed-replicon-beta-gold-aws
```

The seed runs three times and compares scoped row counts plus checksum. It must
not inflate counts across repeated runs.

## Smoke AWS

Run the read-only smoke after deploy and after seed:

```bash
AWS_REGION=us-east-1 \
AWS_APP_INSTANCE_ID=i-xxxxxxxxxxxxxxxxx \
PUBLIC_CONSOLE_URL=http://modecissions-public-...elb.amazonaws.com \
DEPLOY_REF=<release-tag> \
IMAGE_TAG=<release-tag> \
make beta-smoke-aws
```

Evidence is written under `docs/release-evidence/beta-smoke-aws/<UTC>/` and must
include the SSM command id, instance id, region, release refs, UTC time and
public URL. Evidence must be redacted: do not store `.env`, tokens, passwords,
cookies, authorization headers or raw `printenv`.

## Tenant A/B

Run the tenant isolation harness before calling the beta serious:

```bash
make tenant-ab-local

AWS_REGION=us-east-1 \
AWS_APP_INSTANCE_ID=i-xxxxxxxxxxxxxxxxx \
make tenant-ab-aws
```

The harness validates positive access and forbidden probes across API,
Copilot, Gold and Control Room/Superset-equivalent Gold reads.

## Rollback

If `beta-smoke-aws` fails after deploy, rollback to the old immutable tag:

```bash
AWS_REGION=us-east-1 \
AWS_APP_INSTANCE_ID=i-xxxxxxxxxxxxxxxxx \
DEPLOY_REF_OLD=<previous-release-tag> \
IMAGE_TAG_OLD=<previous-release-tag> \
make rollback-aws
```

The rollback wrapper calls `infra/terraform/deploy/rollback.sh`, which backs up
first by default and then runs smoke unless `RUN_SMOKE=0` is explicitly set.

## Backup And DR

Backup can run independently:

```bash
AWS_REGION=us-east-1 AWS_APP_INSTANCE_ID=i-xxxxxxxxxxxxxxxxx make backup-aws
```

DR rehearsal is guarded because it is mutating:

```bash
OMEGA_DR_REHEARSAL_EXECUTE=1 \
AWS_REGION=us-east-1 \
AWS_APP_INSTANCE_ID=i-xxxxxxxxxxxxxxxxx \
make dr-rehearsal-aws
```

