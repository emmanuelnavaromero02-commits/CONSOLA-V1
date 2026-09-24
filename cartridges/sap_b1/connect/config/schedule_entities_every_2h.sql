-- schedule_entities_every_2h.sql
-- Switch every sap_b1 entity from the manual trigger to the platform's
-- scheduled trigger, every two hours, for one tenant/workspace.
--
-- Mechanism (airflow/dags/entity_scheduler.py, infra/init/18_entity_scheduler.sql):
--   the `entity_scheduler` DAG runs every 5 minutes, selects entity_config
--   rows WHERE enabled AND trigger_type = 'scheduled' AND cron_expression IS
--   NOT NULL AND dag_id IS NOT NULL, evaluates cron_expression with croniter
--   in UTC over its 5-minute window, and POSTs one run of dag_id
--   (sap_b1_extract) through the Airflow REST API with
--   conf = {entity, mode, cartridge_id, tenant_id, workspace_id, conn_id,
--   triggered_by}. last_scheduled_at is the idempotency anchor. There is no
--   other per-entity cadence on the platform (cartridge_cycle_config is the
--   SuccessFactors cycle, not a generic scheduler), so this script only
--   writes the columns that DAG reads.
--
-- Usage, on the OMEGA host against the platform database, as a role that may
-- UPDATE entity_config (the migration superuser; entity_config has no RLS):
--   psql "$DATABASE_URL" \
--        -v tenant_id=<TENANT_UUID> -v workspace_id=<WORKSPACE_UUID> \
--        -f cartridges/sap_b1/connect/config/schedule_entities_every_2h.sql
--
-- Prerequisites, in order:
--   1. The cartridge has started once, so entity_config carries its 43 rows
--      (app/services/catalog_service._seed_if_empty). The script aborts otherwise.
--   2. The initial load is done AND the watermarks are seeded
--      (initial_load_by_company_month.md). An incremental cycle without a
--      watermark reads the whole table.
--   3. SCOPE, read before enabling. entity_scheduler passes tenant_id and
--      workspace_id in conf; dags/sap_b1_extract.py forwards them in the
--      request body; but app/api/routes_console.py only honours a trusted,
--      signed `security_context`, and app/core/request_context.scoped_prefix()
--      returns "" without one. Until the sap_b1 DAG signs the context the way
--      cartridges/sap_successfactors/dags/sap_successfactors_extract.py does
--      (_security_context_from_conf), a scheduled run lands UNSCOPED under
--      raw/sap_b1/<entity>/load_date=... instead of
--      raw/sap_b1/<entity>/tenant_id=.../workspace_id=.../load_date=... .
--      Enable one entity first, wait for a cycle, and check the storage_uri of
--      its run (GET /runs on the cartridge) before applying the script to all.
--
-- Cadence: every two hours, UTC, staggered by group so the customer's HANA
-- serves one group's sessions at a time and the silver refresh the cartridge
-- requests after every extraction (job_runner._trigger_silver_refresh) is
-- spread across the window. Lines are read through their header's stamp, so
-- a header and its lines share a slot; a header slot never precedes its
-- lines by more than the same trigger. Counts per slot:
--   :00  masters and finance (15)      :30  journal (2)
--   :10  sales documents (10)          :40  inventory and batches (6)
--   :20  purchase documents (8)        :50  production orders (2)
-- Odd UTC hours instead: replace '*/2' with '1-23/2' in every expression.
-- The cartridge answers each run synchronously; sap_b1_extract keeps
-- Airflow's default max_active_runs, so up to 16 of a slot's runs may open
-- HANA sessions at once. Lower it in the DAG if the customer's DBA asks.

\set ON_ERROR_STOP on

-- Guard 1: seeded catalogue.
SELECT COUNT(*) > 0 AS sap_b1_seeded
FROM entity_config
WHERE cartridge_id = 'sap_b1' \gset
\if :sap_b1_seeded
\else
  \echo 'entity_config has no sap_b1 rows: start the cartridge once so it seeds them, then re-run.'
  \quit 1
\endif

-- Guard 2: the workspace belongs to the tenant (both are FKs on entity_config).
SELECT EXISTS (
    SELECT 1 FROM workspaces
    WHERE id = :'workspace_id'::uuid AND tenant_id = :'tenant_id'::uuid
) AS scope_exists \gset
\if :scope_exists
\else
  \echo 'workspace_id does not belong to tenant_id (or does not exist): nothing changed.'
  \quit 1
\endif

BEGIN;

UPDATE entity_config
SET trigger_type    = 'scheduled',
    dag_id          = COALESCE(NULLIF(dag_id, ''), 'sap_b1_extract'),
    connection_id   = COALESCE(NULLIF(connection_id, ''), 'sap_b1'),
    tenant_id       = :'tenant_id'::uuid,
    workspace_id    = :'workspace_id'::uuid,
    cron_expression = CASE
        WHEN entity IN ('CINF','OADM','OCRN','ORTT','OACT','OFPR','OPRC','OCRG','OSLP','OWHS','OITB','OCRD','OITM','OITT','ITT1')
            THEN '0 */2 * * *'
        WHEN entity IN ('OINV','INV1','ORIN','RIN1','ODLN','DLN1','ORDN','RDN1','ORDR','RDR1')
            THEN '10 */2 * * *'
        WHEN entity IN ('OPCH','PCH1','ORPC','RPC1','OPDN','PDN1','OPOR','POR1')
            THEN '20 */2 * * *'
        WHEN entity IN ('OJDT','JDT1')
            THEN '30 */2 * * *'
        WHEN entity IN ('OINM','IBT1','OITW','OBTN','OBTQ','OIBT')
            THEN '40 */2 * * *'
        WHEN entity IN ('OWOR','WOR1')
            THEN '50 */2 * * *'
        ELSE '0 */2 * * *'
    END
WHERE cartridge_id = 'sap_b1';

-- `enabled` is deliberately not touched: an entity disabled on purpose (OIBT
-- once the customer confirms OBTQ is the table Business One maintains) must
-- not come back because of a cadence change. `mode` stays as seeded:
-- incremental for stamped tables, full for snapshots; the cartridge itself
-- downgrades an incremental request on a snapshot table to full.

COMMIT;

-- Report: what the next entity_scheduler tick will see.
SELECT entity, mode, enabled, trigger_type, cron_expression, dag_id, last_scheduled_at
FROM entity_config
WHERE cartridge_id = 'sap_b1'
ORDER BY cron_expression, entity;

-- Pause the cadence (rows stay, nothing fires):
--   UPDATE entity_config SET trigger_type = 'manual' WHERE cartridge_id = 'sap_b1';
-- Resume without re-running this script:
--   UPDATE entity_config SET trigger_type = 'scheduled'
--    WHERE cartridge_id = 'sap_b1' AND cron_expression IS NOT NULL;
