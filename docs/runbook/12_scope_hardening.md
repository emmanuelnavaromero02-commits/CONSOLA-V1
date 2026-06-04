# Scope Hardening Runbook

This runbook covers the v1.45 stabilization rules for tenant/workspace,
employee ownership, Vault, and pipeline run isolation.

## Invariants

- Customer Vault credentials are never shared globally. Tenant/workspace
  credentials must be written with signed `x-security-context` and stored
  with non-null `tenant_id` and `workspace_id`.
- The only allowed unscoped Vault rows are explicit platform rows:
  `destinations/platform` and global platform secret scopes
  `global`, `platform`, `studio`, `system`, `_system`.
- Unscoped Vault rows outside that allowlist are recorded in
  `vault_legacy_unscoped_entries` and must be migrated into a workspace
  or deleted after operator review.
- Non-platform `pipeline_runs` must carry `tenant_id` and `workspace_id`.
  Only `cartridge_id='platform'` scheduler telemetry may remain global.
- Intelligence data is workspace-scoped for admins and additionally
  `owner_user_id`-scoped for ordinary employees.

## Validation Commands

Run these before calling a scope-hardening release complete:

```bash
.venv/bin/ruff check vault/app/main.py mcp-infra/app/main.py console/app/main.py console/app/routers/studio.py console/app/routers/v1/vault.py
.venv/bin/pytest vault/tests tests/test_vault_audit_log.py tests/test_intelligence_engine_contract.py -q
PYTHONPATH=console .venv/bin/pytest console/tests/test_intelligence_employee_owner_runtime.py -q
PYTHONPATH=refinement .venv/bin/pytest refinement/tests/test_hotfix_runtime.py -q
PYTHONPATH=console .venv/bin/pytest console/tests/test_cross_tenant_api_isolation.py -q
```

The last command starts a real Postgres container and validates native RLS
against production init SQL. It requires Docker Desktop or a reachable Docker
daemon. If Docker is unavailable, do not mark RLS validation complete.

## Legacy Vault Review

After applying migrations, inspect the legacy classification table:

```sql
SELECT scope, cartridge, key, reason, last_seen_at
FROM vault_legacy_unscoped_entries
ORDER BY last_seen_at DESC;
```

For `legacy_global_connection_requires_workspace_migration`, create the
connection through the workspace Settings/Vault UI or API, verify the new
row has `tenant_id` and `workspace_id`, then remove the old unscoped row.

## Production Checks

These queries should return zero for customer credential rows:

```sql
SELECT COUNT(*)
FROM vault_entries
WHERE tenant_id IS NULL
  AND workspace_id IS NULL
  AND scope = 'connections';

SELECT COUNT(*)
FROM pipeline_runs
WHERE cartridge_id <> 'platform'
  AND (tenant_id IS NULL OR workspace_id IS NULL);
```

For employee isolation, validate one ordinary employee and one workspace
admin in the same workspace:

- employee sees only owned intelligence signals/datasets/decisions,
- workspace admin sees all workspace-owned records,
- neither can read another workspace or tenant.
