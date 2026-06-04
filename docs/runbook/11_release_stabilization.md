# Release Stabilization, Metrics, and Rollback

This runbook is the operating contract after a stabilization release. Its goal
is to keep production calm: observe first, avoid tag churn, and rollback with a
known immutable tag if the release degrades.

## Stabilization Rule

- Do not create a new release tag for docs-only or low-risk local cleanup.
- Batch non-urgent fixes behind one stabilization candidate.
- A new production tag is justified only when it fixes a real runtime issue,
  closes a security gap, or ships a tested release candidate.
- Keep `DEPLOY_REF` and `IMAGE_TAG` equal in production. Both must point to the
  same immutable `v*` tag.

## Intelligence Engine Metrics

The operational metrics endpoint is:

```bash
GET /api/metrics/operational
```

It requires `operations.read` and returns an `intelligence` block with:

- `open_signals`
- `high_severity_open_signals`
- `predictive_open_signals`
- `signals_generated_24h`
- `run_count_24h`
- `run_errors_24h`
- `avg_run_duration_ms_24h`
- `avg_signals_per_run_24h`
- `outcomes_recorded_24h`
- `options_selected_24h`
- `measured_outcomes_30d`
- `accurate_outcomes_30d`
- `accuracy_rate_30d`
- `external_source_errors_24h`
- `external_cache_active_items`

Interpretation:

- Rising `run_errors_24h` means the engine or dependencies are failing.
- Rising `external_source_errors_24h` means external context is degraded, but
  numeric signals may still be valid.
- Low `avg_signals_per_run_24h` with green readiness usually means missing or
  stale Gold data, not necessarily a broken engine.
- `accuracy_rate_30d` is `null` until there are measured outcomes. Do not
  market prediction accuracy before outcomes exist.

## Post-Deploy Checks

After every production deploy:

```bash
CONSOLE_URL=https://console.example.com \
OMEGA_MONITOR_REQUIRE_DATA=1 \
bash scripts/monitor_health_once.sh
```

From SSM on the app host:

```bash
cd /opt/modecissions/infra/terraform/deploy
docker compose -f docker-compose.aws.yml ps
curl -fsS http://127.0.0.1:8000/healthz
curl -fsS http://127.0.0.1:8000/readyz
curl -fsS 'http://127.0.0.1:8000/readyz?require_data=1'
```

Then verify the deployed ref:

```bash
cd /opt/modecissions
sudo -u ubuntu git rev-parse --short HEAD
sudo -u ubuntu git describe --tags --exact-match
cd /opt/modecissions/infra/terraform/deploy
sudo bash -lc 'source .env && printf "APP_ENV=%s\nDEPLOY_REF=%s\nIMAGE_TAG=%s\n" "$APP_ENV" "$DEPLOY_REF" "$IMAGE_TAG"'
```

## Rollback Strategy

Rollback is tag-based. Pick the last known-good immutable tag, not `main` and
not `latest`.

1. Confirm current release and previous good release.

```bash
cd /opt/modecissions
sudo -u ubuntu git describe --tags --exact-match
```

2. Run rollback from the app host.

```bash
cd /opt/modecissions/infra/terraform/deploy
RUN_BACKUP_BEFORE_ROLLBACK=1 RUN_SMOKE=1 \
bash rollback.sh v1.45.9-beta
```

3. Validate public readiness.

```bash
CONSOLE_URL=https://console.example.com \
OMEGA_MONITOR_REQUIRE_DATA=1 \
bash /opt/modecissions/scripts/monitor_health_once.sh
```

4. If rollback fails because the target image tag is missing, stop and do not
   try a partial service rollback. Publish or choose a tag that exists for all
   required GHCR images.

## When To Stop Deploying

Freeze release changes when any of these happen:

- More than one hotfix tag is needed for the same functional area.
- `/readyz?require_data=1` is red after a deploy.
- `run_errors_24h` or `external_source_errors_24h` keeps rising after rollback.
- Tenant/workspace isolation tests fail locally or in AWS simulation.
- A migration changes tenant-scoped tables and has not passed direct RLS tests.
- Vault or pipeline scope hardening changes have not passed the checks in
  [12_scope_hardening.md](12_scope_hardening.md).

During freeze, only ship a new tag after:

- local targeted tests pass,
- release workflow passes,
- SSM deploy verification passes,
- public monitor passes.
