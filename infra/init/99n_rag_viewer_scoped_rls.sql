-- Prompt 10: RAG + technical viewer scoped/RLS hardening.
--
-- RAG/vector rows and watermarks are tenant/workspace data. Legacy rows that
-- predate scope are preserved for platform investigation but hidden from
-- tenant/workspace callers by application filters and RLS policies.

CREATE TABLE IF NOT EXISTS omega_rls_platform_owner_allowlist (
    table_name TEXT PRIMARY KEY,
    reason     TEXT NOT NULL,
    expires_on DATE
);

CREATE OR REPLACE FUNCTION omega_rls_workspace_matches(row_tenant uuid, row_workspace uuid)
RETURNS boolean
LANGUAGE sql
STABLE
AS $$
    SELECT
        row_workspace IS NOT NULL
        AND NULLIF(current_setting('app.workspace_id', true), '') IS NOT NULL
        AND row_workspace::text = NULLIF(current_setting('app.workspace_id', true), '')
        AND (
            row_tenant IS NULL
            OR (
                NULLIF(current_setting('app.tenant_id', true), '') IS NOT NULL
                AND row_tenant::text = NULLIF(current_setting('app.tenant_id', true), '')
            )
        )
$$;

ALTER TABLE rag_sources
    ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE,
    ADD COLUMN IF NOT EXISTS workspace_id UUID REFERENCES workspaces(id) ON DELETE CASCADE,
    ADD COLUMN IF NOT EXISTS created_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS visibility TEXT NOT NULL DEFAULT 'orphaned_legacy',
    ADD COLUMN IF NOT EXISTS source_key TEXT,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

ALTER TABLE rag_chunks
    ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE,
    ADD COLUMN IF NOT EXISTS workspace_id UUID REFERENCES workspaces(id) ON DELETE CASCADE;

UPDATE rag_sources
   SET visibility = 'orphaned_legacy'
 WHERE tenant_id IS NULL OR workspace_id IS NULL;

UPDATE rag_chunks c
   SET tenant_id = s.tenant_id,
       workspace_id = s.workspace_id
  FROM rag_sources s
 WHERE c.source_id = s.id
   AND (c.tenant_id IS NULL OR c.workspace_id IS NULL);

CREATE INDEX IF NOT EXISTS idx_rag_sources_workspace_kind
    ON rag_sources(workspace_id, kind, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_rag_chunks_workspace_source
    ON rag_chunks(workspace_id, source_id);

DO $$
DECLARE
    rag_roles text;
    owner_roles text;
BEGIN
    SELECT string_agg(quote_ident(rolname), ', ')
      INTO rag_roles
      FROM pg_roles
     WHERE rolname = ANY(ARRAY['omega_mcp_infra']);

    SELECT string_agg(quote_ident(rolname), ', ')
      INTO owner_roles
      FROM pg_roles
     WHERE rolname = ANY(ARRAY['omega_console', 'omega_refinement']);

    ALTER TABLE rag_sources ENABLE ROW LEVEL SECURITY;
    ALTER TABLE rag_sources FORCE ROW LEVEL SECURITY;
    ALTER TABLE rag_chunks ENABLE ROW LEVEL SECURITY;
    ALTER TABLE rag_chunks FORCE ROW LEVEL SECURITY;

    DROP POLICY IF EXISTS rag_sources_workspace_rls ON rag_sources;
    IF rag_roles IS NOT NULL THEN
        EXECUTE format(
            'CREATE POLICY rag_sources_workspace_rls ON rag_sources
               FOR ALL TO %s
               USING (
                   current_setting(''app.platform_admin'', true) = ''true''
                   OR (
                       visibility <> ''orphaned_legacy''
                       AND omega_rls_workspace_matches(tenant_id, workspace_id)
                   )
               )
               WITH CHECK (
                   current_setting(''app.platform_admin'', true) = ''true''
                   OR omega_rls_workspace_matches(tenant_id, workspace_id)
               )',
            rag_roles
        );
    END IF;

    DROP POLICY IF EXISTS rag_chunks_workspace_rls ON rag_chunks;
    IF rag_roles IS NOT NULL THEN
        EXECUTE format(
            'CREATE POLICY rag_chunks_workspace_rls ON rag_chunks
               FOR ALL TO %s
               USING (
                   current_setting(''app.platform_admin'', true) = ''true''
                   OR omega_rls_workspace_matches(tenant_id, workspace_id)
               )
               WITH CHECK (
                   current_setting(''app.platform_admin'', true) = ''true''
                   OR omega_rls_workspace_matches(tenant_id, workspace_id)
               )',
            rag_roles
        );
    END IF;

    DROP POLICY IF EXISTS rag_sources_platform_owner_rls ON rag_sources;
    DROP POLICY IF EXISTS rag_chunks_platform_owner_rls ON rag_chunks;
    IF owner_roles IS NOT NULL THEN
        EXECUTE format(
            'CREATE POLICY rag_sources_platform_owner_rls ON rag_sources
               FOR ALL TO %s USING (true) WITH CHECK (true)',
            owner_roles
        );
        EXECUTE format(
            'CREATE POLICY rag_chunks_platform_owner_rls ON rag_chunks
               FOR ALL TO %s USING (true) WITH CHECK (true)',
            owner_roles
        );
    END IF;
END $$;

ALTER TABLE entity_watermarks
    ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE,
    ADD COLUMN IF NOT EXISTS workspace_id UUID REFERENCES workspaces(id) ON DELETE CASCADE,
    ADD COLUMN IF NOT EXISTS watermark_scope TEXT;

UPDATE entity_watermarks
   SET watermark_scope = COALESCE(
        watermark_scope,
        CASE
            WHEN tenant_id IS NOT NULL AND workspace_id IS NOT NULL
            THEN 'tenant:' || tenant_id::text || ':workspace:' || workspace_id::text
            ELSE 'platform'
        END
   );

ALTER TABLE entity_watermarks
    ALTER COLUMN watermark_scope SET NOT NULL;

DO $$
DECLARE
    constraint_name text;
BEGIN
    SELECT conname
      INTO constraint_name
      FROM pg_constraint
     WHERE conrelid = 'entity_watermarks'::regclass
       AND contype = 'p'
     LIMIT 1;

    IF constraint_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE entity_watermarks DROP CONSTRAINT %I', constraint_name);
    END IF;
END $$;

ALTER TABLE entity_watermarks
    ADD CONSTRAINT entity_watermarks_pkey
    PRIMARY KEY (watermark_scope, cartridge_id, entity_name);

CREATE INDEX IF NOT EXISTS idx_entity_watermarks_workspace_cartridge
    ON entity_watermarks(workspace_id, cartridge_id, entity_name);

DO $$
DECLARE
    scoped_roles text;
    owner_roles text;
BEGIN
    SELECT string_agg(quote_ident(rolname), ', ')
      INTO scoped_roles
      FROM pg_roles
     WHERE rolname = ANY(ARRAY[
        'omega_mcp_infra',
        'omega_workspace',
        'omega_airflow_dag',
        'omega_cartridge_replicon',
        'omega_cartridge_hubspot',
        'omega_cartridge_salesforce',
        'omega_cartridge_sap_hcm',
        'omega_cartridge_sap_s4',
        'omega_cartridge_sap_sf'
     ]);

    SELECT string_agg(quote_ident(rolname), ', ')
      INTO owner_roles
      FROM pg_roles
     WHERE rolname = ANY(ARRAY['omega_console', 'omega_refinement']);

    ALTER TABLE entity_watermarks ENABLE ROW LEVEL SECURITY;
    ALTER TABLE entity_watermarks FORCE ROW LEVEL SECURITY;

    DROP POLICY IF EXISTS entity_watermarks_workspace_rls ON entity_watermarks;
    IF scoped_roles IS NOT NULL THEN
        EXECUTE format(
            'CREATE POLICY entity_watermarks_workspace_rls ON entity_watermarks
               FOR ALL TO %s
               USING (
                   current_setting(''app.platform_admin'', true) = ''true''
                   OR omega_rls_workspace_matches(tenant_id, workspace_id)
               )
               WITH CHECK (
                   current_setting(''app.platform_admin'', true) = ''true''
                   OR omega_rls_workspace_matches(tenant_id, workspace_id)
               )',
            scoped_roles
        );
    END IF;

    DROP POLICY IF EXISTS entity_watermarks_platform_owner_rls ON entity_watermarks;
    IF owner_roles IS NOT NULL THEN
        EXECUTE format(
            'CREATE POLICY entity_watermarks_platform_owner_rls ON entity_watermarks
               FOR ALL TO %s USING (true) WITH CHECK (true)',
            owner_roles
        );
    END IF;
END $$;

INSERT INTO omega_rls_platform_owner_allowlist (table_name, reason, expires_on)
VALUES
    ('rag_sources', 'platform-only investigation of legacy orphaned RAG sources', NULL),
    ('rag_chunks', 'platform-only investigation of legacy orphaned RAG chunks', NULL),
    ('entity_watermarks', 'platform-only investigation of legacy unscoped watermarks', NULL)
ON CONFLICT (table_name) DO UPDATE
SET reason = EXCLUDED.reason,
    expires_on = EXCLUDED.expires_on;

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99n_rag_viewer_scoped_rls.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
