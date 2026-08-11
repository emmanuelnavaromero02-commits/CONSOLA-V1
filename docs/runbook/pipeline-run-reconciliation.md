# Pipeline run reconciliation

Use `scripts/reconcile_pipeline_runs.py` only when the durable
`pipeline_runs` row is non-terminal after Airflow has recorded a terminal
failure, after its external Airflow history has become unavailable, when an
explicitly verified sync is orphaned, or when an aggregate Airflow success
must be refined to a product-level partial/blocked outcome.

The command does not discover runs and never deletes or inserts a
`pipeline_runs` row. Every target must be listed explicitly with its tenant,
workspace, expected status, exact `started_at`, fencing token, target state,
reason and bounded evidence. Missing rows or changed expectations fail the
whole transaction closed.

## Manifest

```json
{
  "schema": "omega.pipeline-run-reconciliation/v1",
  "change_id": "checkpoint-5.5-stale-runs",
  "runs": [
    {
      "run_id": "sync_now:sap_successfactors:example",
      "tenant_id": "11111111-1111-1111-1111-111111111111",
      "workspace_id": "22222222-2222-2222-2222-222222222222",
      "expected_status": "running",
      "expected_started_at": "2026-08-08T12:00:00+00:00",
      "expected_fencing_token": 0,
      "target_status": "failed",
      "reason": "airflow_run_missing_after_retention",
      "evidence": {
        "airflow_http_status": 404,
        "retention_confirmed": true,
        "observed_at": "2026-08-11T12:00:00+00:00"
      }
    }
  ]
}
```

Accepted reasons are `airflow_terminal_failure`,
`airflow_run_missing_after_retention`, `stale_orphan` and
`aggregate_completed_with_blocks`. A terminal Airflow failure must target
`failed` and its exact observed state must be `failed`, `error`,
`upstream_failed`, `cancelled` or `removed`. A 404 alone is not terminal
evidence; retention must be independently confirmed. For stale sync parents,
`orphan_confirmed` must be true. Aggregate block reconciliation requires an
Airflow success plus at least one blocked child.

## Dry-run and apply

Run dry-run first on the server. It opens a read-only transaction, applies RLS
scope, performs exact lookups, emits the manifest SHA-256 and rolls back:

```sh
python scripts/reconcile_pipeline_runs.py --manifest /server/evidence/pipeline-runs.json
```

Apply requires a server-owned database connection and four server-side
guards. The actor, change id and exact manifest digest are audit metadata, not
credentials:

```sh
export OMEGA_PIPELINE_RECONCILIATION_ALLOW_APPLY=1
export OMEGA_PIPELINE_RECONCILIATION_ACTOR=gcp-release-operator
export OMEGA_PIPELINE_RECONCILIATION_CHANGE_ID=checkpoint-5.5-stale-runs
export OMEGA_PIPELINE_RECONCILIATION_MANIFEST_SHA256=<dry-run digest>
python scripts/reconcile_pipeline_runs.py --manifest /server/evidence/pipeline-runs.json --apply
```

Apply locks each exact scoped row, rechecks status/start/fence, advances only
to a more conservative terminal state, increments the fencing token, clears
the expired lease, appends `extra.reconciliation`, and writes a critical
`audit_events` row in the same transaction. Reapplying the same fingerprint
is a zero-write success. Any mismatch rolls back the complete manifest.

Normal trigger, Airflow refresh, sync and SuccessFactors mirror writers use a
shared monotonic status order and do not replace terminal outcome metadata with
a late lower-status observation. Dataset materialization slots are the sole
intentional exception: their run id represents a reusable slot and a retry may
reopen it only while holding the row lock and incrementing its fencing token.
