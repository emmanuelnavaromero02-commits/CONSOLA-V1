# AWS Deploy, DR, Rollback, and Observability Runbook

This is the official low-cost AWS beta operations path while GitHub Actions
remain disabled for cost control. It uses local commands plus SSM evidence. Do
not paste secrets into commands, logs, or evidence files.

## Scope

- Official deploy path: `make deploy-main-aws`.
- Official full gate: `make aws-full-regression`.
- Official backup: `make backup-aws`.
- Official safe restore rehearsal: `make dr-rehearsal-aws`.
- Official rollback dry-run: `make rollback-rehearsal-aws`.
- Official low-cost observability: `make aws-observability-report`.
- TLS and Superset probes: `make aws-tls-status`, `make aws-superset-probe`.

## Required Environment

```bash
export AWS_REGION=us-east-1
export AWS_APP_INSTANCE_ID=<instance-id>
export PUBLIC_CONSOLE_URL=http://modecissions-public-255609366.us-east-1.elb.amazonaws.com
export DEPLOY_REF=<final-main-sha>
export IMAGE_TAG=v1.45.78-beta
export OMEGA_SEED_TENANT_ID=<beta-tenant-id>
export OMEGA_SEED_WORKSPACE_ID=<beta-workspace-id>
```

`DEPLOY_REF` must be the full SHA of `origin/main` unless the operator passes
`--allow-non-main` directly to `scripts/deploy_main_aws.py` for a break-glass
test. Do not use `latest` as `IMAGE_TAG`.

## Deploy From Main

```bash
make deploy-main-aws
```

The deploy command:

- creates a `git archive` from the exact main SHA;
- calculates a sha256 checksum;
- uploads the archive to the configured S3 bucket;
- downloads and verifies it on the EC2 host through SSM;
- preserves the existing host worktree in `/opt/modecissions-deploy-backups`;
- preserves `.env` without copying secret values into local evidence;
- updates `/opt/modecissions`, `DEPLOY_REF`, `IMAGE_TAG`, and `VERSION`;
- runs Docker Compose config validation;
- optionally applies DB migrations;
- recreates runtime app services;
- validates internal `/healthz`, `/readyz`, and `/readyz?require_data=1`.

Evidence is written to:

```text
docs/release-evidence/deploy-main-aws/<UTC>/
```

## Full Regression

```bash
make aws-full-regression
```

This orchestrates:

- `beta-smoke-aws`;
- `tenant-ab-aws`;
- `decision-backtest-aws`;
- Control Room cycle proof;
- observability report;
- Superset probe;
- TLS status probe.

Critical failures return non-zero. TLS and deeper Superset tenant propagation
may return `BLOCKED` with an explicit P1 unblock plan instead of a false PASS.

Evidence is written to:

```text
docs/release-evidence/aws-full-regression/<UTC>/
```

## Backup

```bash
make backup-aws
```

The backup includes:

- operational Postgres dump and checksum;
- Gold Postgres dump and checksum;
- lakehouse object manifest and checksum;
- config manifest with keys only and secret values redacted;
- deploy metadata and restore hint.

The wrapper fails if the backup manifest cannot be parsed as verifiable
evidence. Dumps are uploaded to S3 only; they are never written into the repo.

## Safe DR Rehearsal

```bash
BACKUP_ID=<backup-id> make dr-rehearsal-aws
```

Default DR rehearsal is non-destructive. It downloads the backup, verifies gzip
streams, starts temporary Postgres containers with no host ports, restores both
dumps into those isolated containers, records table counts, and cleans up.

It must not restore over live databases and must not write to lakehouse
production prefixes.

## Rollback Rehearsal

```bash
IMAGE_TAG_OLD=<previous-v-tag> make rollback-rehearsal-aws
```

Rollback defaults to dry-run. A real rollback requires:

```bash
CONFIRM_ROLLBACK=1 IMAGE_TAG_OLD=<previous-v-tag> make rollback-aws
```

The dry-run validates the target immutable image exists and records current
release metadata. Do not leave AWS on the old version after a rehearsal.

## Observability, TLS, and Superset

```bash
make aws-observability-report
make aws-tls-status
make aws-superset-probe
```

Observability is intentionally lightweight: public health, internal health,
container status, Postgres, Gold, Redis, lakehouse object listing, backup
presence, disk, memory, recent console error count, failed action/intelligence/
backtest runs, deploy ref, image tag, and external write-back flag.

TLS status is an honest PASS/BLOCKED/FAIL report. It does not create ACM
certificates, listeners, DNS, or other paid resources.

Superset probe validates health, Gold connectivity, Gold role posture, and RLS
basics. Per-user tenant context propagation remains P1 unless explicitly
validated by a dashboard/session probe.

## Safety Rules

- Do not run `make nuke`.
- Do not run destructive restore on live databases.
- Do not enable external write-back.
- Do not print or store `.env`, tokens, passwords, cookies, headers, or
  `printenv` output.
- If any critical gate fails after deploy, use the rollback dry-run evidence to
  decide whether to execute confirmed rollback.
