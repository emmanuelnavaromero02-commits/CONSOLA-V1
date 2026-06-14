# Platform cartridge

Shared, default-enabled cartridge that ships the **system DAGs** every other
cartridge reuses. It owns no data and connects to no external system.

## What it provides

| DAG | Role | What it does |
|-----|------|--------------|
| `entity_scheduler` | orchestrator | Meta-scheduler. Every few minutes reads `entity_config` and triggers each entity's base DAG, passing `entity` / `mode` / `cartridge_id` via `conf`. Lets many entities share one base DAG with independent cadence. |
| `agent_runner` | orchestrator | Fires agents whose `extra.schedule.cron` falls in the current window. |
| `dataset_refresh_chain` | orchestrator | Propagates downstream materializations per `datasets.sources`. |
| `file_ingest` | worker | Generic file (csv/excel) → bronze parquet ingest, reusable by any cartridge. |

## Why it's a cartridge

The platform's orchestration was previously loose files under `airflow/dags/`.
Packaging them as a cartridge makes them versioned and shippable like any other,
and lets `entity_scheduler` resolve a base DAG from the owning cartridge **or**
fall back to `platform` (`cartridge_dags.cartridge_id IN (<cartridge>, 'platform')`).
That fallback is what makes a single platform DAG usable across all cartridges.

## Default activation

This cartridge is **active by default** for every tenant — it is infrastructure,
not an opt-in data product. `connector.yaml` sets `default_enabled: true`, and
console treats `cartridge_id = 'platform'` as cross-tenant (exempt from the
per-tenant data scoping applied to data cartridges).

## Packaging model (for reference)

A cartridge is defined **once** in the platform (`config/seed.sql` →
`cartridges`, `cartridge_dags`, …). It is **not** copied per tenant. Data
cartridges are activated/deactivated per tenant via `tenant_entitlements` +
`cartridge_installations`; data is isolated by `tenant_id` / `workspace_id`.
The platform cartridge skips that opt-in and is always on.
