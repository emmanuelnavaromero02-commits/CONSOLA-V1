# OMEGA Enterprise Readiness 20x

This gate validates the production-facing OMEGA surfaces with evidence, not
optimistic smoke checks. A run is complete only when load evidence and the final
data integrity audit agree.

## Primary Command

```bash
PUBLIC_CONSOLE_URL=http://modecissions-public-255609366.us-east-1.elb.amazonaws.com \
make enterprise-readiness TARGET=aws WORKLOAD=sap_successfactors PROFILE=beta-safe
```

Evidence is written to:

```text
docs/release-evidence/enterprise-readiness/<run_id>/
```

The run writes `summary.json`, `REPORT.md`, per-command logs, Locust HTML/CSV,
p95/p99 summaries, Copilot redteam evidence, cartridge resilience evidence and
data integrity audit reports.

## PASS / FAIL / BLOCKED

- `PASS`: the check ran and met its threshold.
- `FAIL`: the check ran and found a release-blocking defect.
- `BLOCKED`: the check cannot be honestly executed yet because it needs live
  credentials, a dedicated staging environment, destructive AWS permission or
  database/lakehouse access. A blocked check must include the exact command or
  environment variable needed to unblock it.

These are hard failures:

- any cross-tenant or cross-workspace leak;
- any visible secret, PEM, password or token;
- any mutation without approval;
- any refresh duplication or corrupt parquet;
- p95 normal reads above 800 ms or p99 above 2.5 s;
- 5xx error rate above 1%;
- any job stuck beyond the configured audit threshold.

## Production Safety

`TARGET=aws PROFILE=beta-safe` does not execute destructive chaos against the
current production AWS instance. `chaos-aws`, DR restore and rollback rehearsal
remain guarded until a dedicated staging target is confirmed.

To unblock destructive AWS chaos:

```bash
PUBLIC_CONSOLE_URL=https://staging.example.test \
OMEGA_V1_STRESS_TARGET=staging \
OMEGA_V1_STRESS_ALLOW_CHAOS=1 \
OMEGA_V1_GA_DEDICATED_STAGING=1 \
make chaos-aws
```

## Individual Targets

```bash
make stress-smoke WORKLOAD=sap_successfactors
make stress-beta WORKLOAD=sap_successfactors
make stress-spike WORKLOAD=sap_successfactors
make stress-breakpoint WORKLOAD=sap_successfactors
make stress-soak-24h WORKLOAD=sap_successfactors
make stress-write-heavy WORKLOAD=sap_successfactors
make data-integrity-audit
make copilot-redteam
make cartridge-resilience WORKLOAD=sap_successfactors
make chaos-local
make chaos-aws
make rollback-rehearsal
```

Every `stress-*` target runs `data-integrity-audit` after Locust, even if Locust
fails, so evidence is retained before the target returns its final status.
