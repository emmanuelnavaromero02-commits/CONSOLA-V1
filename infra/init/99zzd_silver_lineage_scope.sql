-- Scope lineage relationally. Historical rows have no trustworthy relational
-- provenance, so they are preserved outside every runtime role instead of
-- deriving ownership from storage_uri text.
BEGIN;

CREATE SCHEMA IF NOT EXISTS omega_quarantine AUTHORIZATION postgres;
REVOKE ALL ON SCHEMA omega_quarantine FROM PUBLIC;

CREATE TABLE IF NOT EXISTS omega_quarantine.silver_lineage_legacy (
    legacy_lineage_id BIGINT PRIMARY KEY,
    silver_name TEXT NOT NULL,
    cartridge_id TEXT NOT NULL,
    source_entity TEXT NOT NULL,
    source_load_date DATE,
    source_batch_id TEXT,
    sql_def TEXT NOT NULL,
    column_mapping JSONB,
    layer TEXT,
    row_count BIGINT,
    storage_uri TEXT,
    created_by TEXT,
    created_at TIMESTAMPTZ,
    quarantine_reason TEXT NOT NULL,
    quarantined_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
REVOKE ALL ON TABLE omega_quarantine.silver_lineage_legacy FROM PUBLIC;

LOCK TABLE silver_lineage IN ACCESS EXCLUSIVE MODE;
ALTER TABLE silver_lineage ADD COLUMN IF NOT EXISTS tenant_id UUID;
ALTER TABLE silver_lineage ADD COLUMN IF NOT EXISTS workspace_id UUID;
ALTER TABLE silver_lineage
    ADD COLUMN IF NOT EXISTS scope_status TEXT NOT NULL DEFAULT 'legacy_unscoped';

CREATE TEMP TABLE silver_lineage_unverifiable_ids ON COMMIT DROP AS
SELECT id
  FROM silver_lineage
 WHERE tenant_id IS NULL OR workspace_id IS NULL OR scope_status <> 'scoped';

INSERT INTO omega_quarantine.silver_lineage_legacy (
    legacy_lineage_id, silver_name, cartridge_id, source_entity,
    source_load_date, source_batch_id, sql_def, column_mapping, layer,
    row_count, storage_uri, created_by, created_at, quarantine_reason
)
SELECT lineage.id, lineage.silver_name, lineage.cartridge_id,
       lineage.source_entity, lineage.source_load_date,
       lineage.source_batch_id, lineage.sql_def, lineage.column_mapping,
       lineage.layer, lineage.row_count, lineage.storage_uri,
       lineage.created_by, lineage.created_at, 'scope_unverifiable'
  FROM silver_lineage AS lineage
  JOIN silver_lineage_unverifiable_ids AS legacy ON legacy.id = lineage.id
ON CONFLICT (legacy_lineage_id) DO NOTHING;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM silver_lineage_unverifiable_ids AS legacy
          LEFT JOIN omega_quarantine.silver_lineage_legacy AS quarantine
            ON quarantine.legacy_lineage_id = legacy.id
         WHERE quarantine.legacy_lineage_id IS NULL
    ) THEN
        RAISE EXCEPTION 'silver lineage quarantine verification failed';
    END IF;
END $$;

DELETE FROM silver_lineage AS lineage
 USING silver_lineage_unverifiable_ids AS legacy
 WHERE lineage.id = legacy.id;

ALTER TABLE silver_lineage ALTER COLUMN tenant_id SET NOT NULL;
ALTER TABLE silver_lineage ALTER COLUMN workspace_id SET NOT NULL;
ALTER TABLE silver_lineage ALTER COLUMN scope_status SET DEFAULT 'scoped';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.silver_lineage'::regclass
           AND conname = 'silver_lineage_scope_status_check'
    ) THEN
        ALTER TABLE silver_lineage
            ADD CONSTRAINT silver_lineage_scope_status_check
            CHECK (scope_status = 'scoped') NOT VALID;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.silver_lineage'::regclass
           AND conname = 'silver_lineage_tenant_fk'
    ) THEN
        ALTER TABLE silver_lineage
            ADD CONSTRAINT silver_lineage_tenant_fk
            FOREIGN KEY (tenant_id) REFERENCES tenants(id)
            ON DELETE RESTRICT NOT VALID;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.silver_lineage'::regclass
           AND conname = 'silver_lineage_workspace_scope_fk'
    ) THEN
        ALTER TABLE silver_lineage
            ADD CONSTRAINT silver_lineage_workspace_scope_fk
            FOREIGN KEY (tenant_id, workspace_id)
            REFERENCES workspaces(tenant_id, id) ON DELETE RESTRICT NOT VALID;
    END IF;
END $$;

ALTER TABLE silver_lineage VALIDATE CONSTRAINT silver_lineage_scope_status_check;
ALTER TABLE silver_lineage VALIDATE CONSTRAINT silver_lineage_tenant_fk;
ALTER TABLE silver_lineage VALIDATE CONSTRAINT silver_lineage_workspace_scope_fk;
CREATE INDEX IF NOT EXISTS silver_lineage_scope_dataset_created_idx
    ON silver_lineage(
        tenant_id, workspace_id, cartridge_id, silver_name, layer,
        created_at DESC, id DESC
    );

-- Older installs can retain the global index created by 67_data_catalog even
-- after 99w introduced workspace-scoped uniqueness. It makes the same
-- dataset/column collide across otherwise isolated workspaces.
DO $$
BEGIN
    IF to_regclass('public.data_catalog') IS NOT NULL THEN
        LOCK TABLE data_catalog IN SHARE ROW EXCLUSIVE MODE;
        DROP INDEX IF EXISTS data_catalog_dataset_column_uq;
        ALTER TABLE data_catalog
            DROP CONSTRAINT IF EXISTS data_catalog_dataset_column_name_key;
        CREATE UNIQUE INDEX IF NOT EXISTS data_catalog_scoped_dataset_column_key
            ON data_catalog(workspace_id, dataset, column_name)
         WHERE workspace_id IS NOT NULL;
        CREATE UNIQUE INDEX IF NOT EXISTS data_catalog_legacy_dataset_column_key
            ON data_catalog(dataset, column_name)
         WHERE workspace_id IS NULL;
    END IF;
END $$;

ALTER TABLE silver_lineage ENABLE ROW LEVEL SECURITY;
ALTER TABLE silver_lineage FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS silver_lineage_scope_read ON silver_lineage;
DROP POLICY IF EXISTS silver_lineage_scope_insert ON silver_lineage;
CREATE POLICY silver_lineage_scope_read ON silver_lineage
    FOR SELECT TO omega_console, omega_refinement
    USING (omega_rls_workspace_matches(tenant_id, workspace_id));
CREATE POLICY silver_lineage_scope_insert ON silver_lineage
    FOR INSERT TO omega_refinement
    WITH CHECK (omega_rls_workspace_matches(tenant_id, workspace_id));

REVOKE ALL ON silver_lineage FROM PUBLIC;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON silver_lineage
    FROM omega_console, omega_mcp_infra, omega_airflow_dag;
REVOKE UPDATE, DELETE, TRUNCATE ON silver_lineage FROM omega_refinement;
GRANT SELECT ON silver_lineage TO omega_console, omega_refinement;
GRANT INSERT ON silver_lineage TO omega_refinement;
GRANT USAGE, SELECT ON SEQUENCE silver_lineage_id_seq TO omega_refinement;
REVOKE ALL ON SCHEMA omega_quarantine FROM
    omega_console, omega_refinement, omega_mcp_infra, omega_airflow_dag;

ALTER ROLE omega_console NOBYPASSRLS;
ALTER ROLE omega_refinement NOBYPASSRLS;
ALTER ROLE omega_mcp_infra NOBYPASSRLS;
ALTER ROLE omega_airflow_dag NOBYPASSRLS;

INSERT INTO schema_migrations(filename, applied_at)
VALUES ('99zzd_silver_lineage_scope.sql', NOW())
ON CONFLICT (filename) DO NOTHING;

COMMIT;
