# AWS / HTTPS Staging Checklist

Status: BLOCKED until a real public staging deployment is available.

## Required Inputs

- `PUBLIC_CONSOLE_URL=https://...`
- `PUBLIC_WORKSPACE_URL=https://...`
- `TEST_EMAIL`
- `TEST_PASSWORD` or `E2E_ADMIN_PASSWORD`
- `E2E_LIVE_LLM=1`
- `ANTHROPIC_API_KEY`
- Live cartridge sandbox credentials loaded and confirmed.

## Required AWS Controls

- GitHub Actions deploy uses OIDC, not long-lived AWS keys.
- Deploy job is protected by the `production` GitHub environment.
- Runtime secrets are loaded from AWS Secrets Manager / SSM, not manually
  copied `.env` files.
- Console and Workspace are public only through HTTPS ALB listeners.
- Airflow, Superset, MCP, cartridges, Postgres, and MinIO remain behind
  VPN/SSM or private networking.
- Rollback is tag based: `DEPLOY_REF` and `IMAGE_TAG` must point to the same
  immutable tag.

## Verification Command

```bash
PUBLIC_CONSOLE_URL=https://console.example.com \
PUBLIC_WORKSPACE_URL=https://workspace.example.com \
TEST_EMAIL=admin@example.com \
TEST_PASSWORD=<staging-admin-password> \
E2E_LIVE_LLM=1 \
ANTHROPIC_API_KEY=<live-key> \
make verify-v1-public
```

## Required Evidence

- `make verify-v1-public` passes against public HTTPS URLs.
- `/healthz` and `/readyz` pass over HTTPS.
- HTTP redirects to HTTPS.
- Direct internal ports are not reachable from the public host.
- Login cookies are `HttpOnly`, `Secure`, and `SameSite`.
- All six priority cartridges return `ok` from live `test_connection`.
- Playwright passes against public Console.
- EC2/ECS restart returns to healthy state without manual reconcile.
- `backup.sh`, `restore.sh`, and `rollback.sh <tag>` run on staging and end
  with green smoke/readiness.

## Current Local Attempt

Command:

```bash
make verify-v1-public
```

Expected in this local workspace without staging URLs: BLOCKED because
`PUBLIC_CONSOLE_URL` and `PUBLIC_WORKSPACE_URL` are not configured.

Output:

```text
[verify-v1-public] ERROR: PUBLIC_CONSOLE_URL is required
make: *** [verify-v1-public] Error 1
```
