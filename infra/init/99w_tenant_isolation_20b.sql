-- Prompt 20B — Tenant isolation & data exposure hardening.
--
-- Classification manifest:
--   copilot_drafts                 user_private
--   user_facts                     user_private
--   user_preferences               user_private
--   conversation_memory_summary    user_private (via conversation/workspace)
--   workflow_runs                  workspace_data
--   workflow_steps                 workspace_data (child of workflow_runs)
--   data_catalog                   workspace_data when tied to datasets
--   data_relationships             workspace_data when both datasets share scope
--   analytic_apps                  platform_template for packaged shared apps;
--                                  workspace_data for user-created/private apps
--
-- Backfill rule: only infer scope from trusted links. Rows whose tenant/workspace
-- cannot be inferred with high confidence remain preserved as legacy_unscoped;
-- tenant-scoped sessions cannot read them, while platform-admin/service audit can.

CREATE OR REPLACE FUNCTION omega_rls_tenant_matches(row_tenant uuid)
RETURNS boolean
LANGUAGE sql
STABLE
AS $$
    SELECT row_tenant IS NOT NULL
       AND NULLIF(current_setting('app.tenant_id', true), '') IS NOT NULL
       AND row_tenant::text = NULLIF(current_setting('app.tenant_id', true), '')
$$;

CREATE OR REPLACE FUNCTION omega_rls_workspace_matches(row_tenant uuid, row_workspace uuid)
RETURNS boolean
LANGUAGE sql
STABLE
AS $$
    SELECT row_workspace IS NOT NULL
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

CREATE OR REPLACE FUNCTION omega_20b_platform_audit_context()
RETURNS boolean
LANGUAGE sql
STABLE
AS $$
    SELECT NULLIF(current_setting('app.workspace_id', true), '') IS NULL
$$;

DO $$
DECLARE
    tbl text;
    scoped_tables text[] := ARRAY[
        'copilot_drafts',
        'workflow_runs',
        'workflow_steps',
        'user_facts',
        'user_preferences',
        'conversation_memory_summary',
        'analytic_apps',
        'data_catalog',
        'data_relationships'
    ];
BEGIN
    FOREACH tbl IN ARRAY scoped_tables LOOP
        IF to_regclass(format('public.%I', tbl)) IS NULL THEN
            CONTINUE;
        END IF;

        EXECUTE format(
            'ALTER TABLE public.%I ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id) ON DELETE SET NULL',
            tbl
        );
        EXECUTE format(
            'ALTER TABLE public.%I ADD COLUMN IF NOT EXISTS workspace_id UUID REFERENCES workspaces(id) ON DELETE SET NULL',
            tbl
        );
        EXECUTE format(
            'ALTER TABLE public.%I ADD COLUMN IF NOT EXISTS scope_status TEXT NOT NULL DEFAULT ''legacy_unscoped''',
            tbl
        );

        IF NOT EXISTS (
            SELECT 1
              FROM pg_constraint c
              JOIN pg_class rel ON rel.oid = c.conrelid
              JOIN pg_namespace nsp ON nsp.oid = rel.relnamespace
             WHERE nsp.nspname = 'public'
               AND rel.relname = tbl
               AND c.conname = tbl || '_scope_status_check'
        ) THEN
            EXECUTE format(
                'ALTER TABLE public.%I ADD CONSTRAINT %I CHECK (scope_status IN (''scoped'', ''platform_template'', ''platform_only'', ''legacy_unscoped'')) NOT VALID',
                tbl,
                tbl || '_scope_status_check'
            );
        END IF;
    END LOOP;
END $$;

-- Existing uniqueness was global. Replace it with scoped uniqueness so two
-- workspaces can document a dataset/column name independently.
DO $$
BEGIN
    IF to_regclass('public.user_facts') IS NOT NULL THEN
        ALTER TABLE public.user_facts DROP CONSTRAINT IF EXISTS user_facts_user_id_fact_key;
        CREATE UNIQUE INDEX IF NOT EXISTS user_facts_scoped_key
            ON user_facts(user_id, workspace_id, fact)
         WHERE workspace_id IS NOT NULL;
        CREATE UNIQUE INDEX IF NOT EXISTS user_facts_legacy_key
            ON user_facts(user_id, fact)
         WHERE workspace_id IS NULL;
    END IF;

    IF to_regclass('public.user_preferences') IS NOT NULL THEN
        ALTER TABLE public.user_preferences DROP CONSTRAINT IF EXISTS user_preferences_pkey;
        CREATE UNIQUE INDEX IF NOT EXISTS user_preferences_scoped_key
            ON user_preferences(user_id, workspace_id, pref_key)
         WHERE workspace_id IS NOT NULL;
        CREATE UNIQUE INDEX IF NOT EXISTS user_preferences_legacy_key
            ON user_preferences(user_id, pref_key)
         WHERE workspace_id IS NULL;
    END IF;

    IF to_regclass('public.data_catalog') IS NOT NULL THEN
        ALTER TABLE public.data_catalog DROP CONSTRAINT IF EXISTS data_catalog_dataset_column_name_key;
        CREATE UNIQUE INDEX IF NOT EXISTS data_catalog_scoped_dataset_column_key
            ON data_catalog(workspace_id, dataset, column_name)
         WHERE workspace_id IS NOT NULL;
        CREATE UNIQUE INDEX IF NOT EXISTS data_catalog_legacy_dataset_column_key
            ON data_catalog(dataset, column_name)
         WHERE workspace_id IS NULL;
    END IF;

    IF to_regclass('public.data_relationships') IS NOT NULL THEN
        ALTER TABLE public.data_relationships DROP CONSTRAINT IF EXISTS data_relationships_from_dataset_from_column_to_dataset_to_column_key;
        CREATE UNIQUE INDEX IF NOT EXISTS data_relationships_scoped_key
            ON data_relationships(workspace_id, from_dataset, from_column, to_dataset, to_column)
         WHERE workspace_id IS NOT NULL;
        CREATE UNIQUE INDEX IF NOT EXISTS data_relationships_legacy_key
            ON data_relationships(from_dataset, from_column, to_dataset, to_column)
         WHERE workspace_id IS NULL;
    END IF;
END $$;

DO $$
DECLARE
    tbl text;
BEGIN
    FOREACH tbl IN ARRAY ARRAY[
        'copilot_drafts',
        'workflow_runs',
        'workflow_steps',
        'user_facts',
        'user_preferences',
        'conversation_memory_summary',
        'analytic_apps',
        'data_catalog',
        'data_relationships'
    ] LOOP
        IF to_regclass(format('public.%I', tbl)) IS NOT NULL THEN
            EXECUTE format('CREATE INDEX IF NOT EXISTS %I ON public.%I(tenant_id, workspace_id)', tbl || '_tenant_workspace_idx', tbl);
            EXECUTE format('CREATE INDEX IF NOT EXISTS %I ON public.%I(scope_status)', tbl || '_scope_status_idx', tbl);
        END IF;
    END LOOP;
END $$;

-- High-confidence user scope: exactly one workspace membership, linked to the
-- tenant on users/workspaces.
WITH user_scope AS (
    SELECT u.id AS user_id,
           COALESCE(u.tenant_id, MIN(w.tenant_id::text)::uuid) AS tenant_id,
           MIN(w.id::text)::uuid AS workspace_id
      FROM users u
      JOIN user_workspace_roles uwr ON uwr.user_id = u.id
      JOIN workspaces w ON w.id = uwr.workspace_id
     GROUP BY u.id, u.tenant_id
    HAVING COUNT(DISTINCT w.id) = 1
       AND COALESCE(u.tenant_id, MIN(w.tenant_id::text)::uuid) IS NOT NULL
)
UPDATE user_facts f
   SET tenant_id = us.tenant_id,
       workspace_id = us.workspace_id,
       scope_status = 'scoped'
  FROM user_scope us
 WHERE f.user_id = us.user_id
   AND (f.tenant_id IS NULL OR f.workspace_id IS NULL OR f.scope_status <> 'scoped');

WITH user_scope AS (
    SELECT u.id AS user_id,
           COALESCE(u.tenant_id, MIN(w.tenant_id::text)::uuid) AS tenant_id,
           MIN(w.id::text)::uuid AS workspace_id
      FROM users u
      JOIN user_workspace_roles uwr ON uwr.user_id = u.id
      JOIN workspaces w ON w.id = uwr.workspace_id
     GROUP BY u.id, u.tenant_id
    HAVING COUNT(DISTINCT w.id) = 1
       AND COALESCE(u.tenant_id, MIN(w.tenant_id::text)::uuid) IS NOT NULL
)
UPDATE user_preferences p
   SET tenant_id = us.tenant_id,
       workspace_id = us.workspace_id,
       scope_status = 'scoped'
  FROM user_scope us
 WHERE p.user_id = us.user_id
   AND (p.tenant_id IS NULL OR p.workspace_id IS NULL OR p.scope_status <> 'scoped');

WITH user_scope AS (
    SELECT u.id AS user_id,
           COALESCE(u.tenant_id, MIN(w.tenant_id::text)::uuid) AS tenant_id,
           MIN(w.id::text)::uuid AS workspace_id
      FROM users u
      JOIN user_workspace_roles uwr ON uwr.user_id = u.id
      JOIN workspaces w ON w.id = uwr.workspace_id
     GROUP BY u.id, u.tenant_id
    HAVING COUNT(DISTINCT w.id) = 1
       AND COALESCE(u.tenant_id, MIN(w.tenant_id::text)::uuid) IS NOT NULL
)
UPDATE copilot_drafts d
   SET tenant_id = us.tenant_id,
       workspace_id = us.workspace_id,
       scope_status = 'scoped'
  FROM user_scope us
 WHERE d.user_id = us.user_id
   AND (d.tenant_id IS NULL OR d.workspace_id IS NULL OR d.scope_status <> 'scoped');

UPDATE conversation_memory_summary cms
   SET tenant_id = w.tenant_id,
       workspace_id = c.workspace_id,
       scope_status = 'scoped'
  FROM conversations c
  JOIN workspaces w ON w.id = c.workspace_id
 WHERE cms.conversation_id = c.id
   AND c.workspace_id IS NOT NULL
   AND (cms.tenant_id IS NULL OR cms.workspace_id IS NULL OR cms.scope_status <> 'scoped');

UPDATE workflow_runs wr
   SET tenant_id = w.tenant_id,
       workspace_id = c.workspace_id,
       scope_status = 'scoped'
  FROM conversations c
  JOIN workspaces w ON w.id = c.workspace_id
 WHERE wr.conversation_id = c.id
   AND c.workspace_id IS NOT NULL
   AND (wr.tenant_id IS NULL OR wr.workspace_id IS NULL OR wr.scope_status <> 'scoped');

WITH user_scope AS (
    SELECT u.id AS user_id,
           COALESCE(u.tenant_id, MIN(w.tenant_id::text)::uuid) AS tenant_id,
           MIN(w.id::text)::uuid AS workspace_id
      FROM users u
      JOIN user_workspace_roles uwr ON uwr.user_id = u.id
      JOIN workspaces w ON w.id = uwr.workspace_id
     GROUP BY u.id, u.tenant_id
    HAVING COUNT(DISTINCT w.id) = 1
       AND COALESCE(u.tenant_id, MIN(w.tenant_id::text)::uuid) IS NOT NULL
)
UPDATE workflow_runs wr
   SET tenant_id = us.tenant_id,
       workspace_id = us.workspace_id,
       scope_status = 'scoped'
  FROM user_scope us
 WHERE wr.user_id = us.user_id
   AND wr.scope_status <> 'scoped';

UPDATE workflow_steps ws
   SET tenant_id = wr.tenant_id,
       workspace_id = wr.workspace_id,
       scope_status = 'scoped'
  FROM workflow_runs wr
 WHERE ws.workflow_id = wr.id
   AND wr.scope_status = 'scoped'
   AND wr.workspace_id IS NOT NULL
   AND (ws.tenant_id IS NULL OR ws.workspace_id IS NULL OR ws.scope_status <> 'scoped');

UPDATE data_catalog dc
   SET tenant_id = w.tenant_id,
       workspace_id = d.workspace_id,
       scope_status = 'scoped'
  FROM datasets d
  JOIN workspaces w ON w.id = d.workspace_id
 WHERE dc.dataset = d.name
   AND d.workspace_id IS NOT NULL
   AND (dc.tenant_id IS NULL OR dc.workspace_id IS NULL OR dc.scope_status <> 'scoped');

WITH relationship_scope AS (
    SELECT dr.id,
           df.workspace_id,
           wf.tenant_id
      FROM data_relationships dr
      JOIN datasets df ON df.name = dr.from_dataset
      JOIN datasets dt ON dt.name = dr.to_dataset
      JOIN workspaces wf ON wf.id = df.workspace_id
      JOIN workspaces wt ON wt.id = dt.workspace_id
     WHERE df.workspace_id = dt.workspace_id
       AND wf.tenant_id = wt.tenant_id
)
UPDATE data_relationships dr
   SET tenant_id = rs.tenant_id,
       workspace_id = rs.workspace_id,
       scope_status = 'scoped'
  FROM relationship_scope rs
 WHERE dr.id = rs.id
   AND (dr.tenant_id IS NULL OR dr.workspace_id IS NULL OR dr.scope_status <> 'scoped');

UPDATE analytic_apps
   SET tenant_id = NULL,
       workspace_id = NULL,
       scope_status = 'platform_template'
 WHERE created_by_id IS NULL
   AND COALESCE(visibility, 'private') IN ('shared', 'public');

WITH user_scope AS (
    SELECT u.id AS user_id,
           COALESCE(u.tenant_id, MIN(w.tenant_id::text)::uuid) AS tenant_id,
           MIN(w.id::text)::uuid AS workspace_id
      FROM users u
      JOIN user_workspace_roles uwr ON uwr.user_id = u.id
      JOIN workspaces w ON w.id = uwr.workspace_id
     GROUP BY u.id, u.tenant_id
    HAVING COUNT(DISTINCT w.id) = 1
       AND COALESCE(u.tenant_id, MIN(w.tenant_id::text)::uuid) IS NOT NULL
)
UPDATE analytic_apps aa
   SET tenant_id = us.tenant_id,
       workspace_id = us.workspace_id,
       scope_status = 'scoped'
  FROM user_scope us
 WHERE aa.created_by_id = us.user_id
   AND (aa.tenant_id IS NULL OR aa.workspace_id IS NULL OR aa.scope_status <> 'scoped');

DO $$
DECLARE
    role_name text;
BEGIN
    FOREACH role_name IN ARRAY ARRAY['omega_console', 'omega_refinement', 'omega_mcp_infra'] LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role_name) THEN
            EXECUTE format('ALTER ROLE %I NOBYPASSRLS', role_name);
        END IF;
    END LOOP;
END $$;

DO $$
DECLARE
    tbl text;
BEGIN
    FOREACH tbl IN ARRAY ARRAY[
        'copilot_drafts',
        'workflow_runs',
        'workflow_steps',
        'user_facts',
        'user_preferences',
        'conversation_memory_summary'
    ] LOOP
        IF to_regclass(format('public.%I', tbl)) IS NULL THEN
            CONTINUE;
        END IF;
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', tbl);
        EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY', tbl);
        EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', tbl || '_20b_scoped_rw', tbl);
        EXECUTE format(
            'CREATE POLICY %I ON public.%I FOR ALL TO omega_console USING (scope_status = ''scoped'' AND omega_rls_workspace_matches(tenant_id, workspace_id)) WITH CHECK (scope_status = ''scoped'' AND omega_rls_workspace_matches(tenant_id, workspace_id))',
            tbl || '_20b_scoped_rw',
            tbl
        );
        EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', tbl || '_20b_legacy_platform_audit', tbl);
        EXECUTE format(
            'CREATE POLICY %I ON public.%I FOR SELECT TO omega_console USING (scope_status = ''legacy_unscoped'' AND omega_20b_platform_audit_context())',
            tbl || '_20b_legacy_platform_audit',
            tbl
        );
    END LOOP;
END $$;

DO $$
DECLARE
    tbl text;
BEGIN
    FOREACH tbl IN ARRAY ARRAY['data_catalog', 'data_relationships'] LOOP
        IF to_regclass(format('public.%I', tbl)) IS NULL THEN
            CONTINUE;
        END IF;
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', tbl);
        EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY', tbl);
        EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', tbl || '_20b_scoped_rw', tbl);
        EXECUTE format(
            'CREATE POLICY %I ON public.%I FOR ALL TO omega_console, omega_refinement, omega_mcp_infra USING (scope_status = ''scoped'' AND omega_rls_workspace_matches(tenant_id, workspace_id)) WITH CHECK (scope_status = ''scoped'' AND omega_rls_workspace_matches(tenant_id, workspace_id))',
            tbl || '_20b_scoped_rw',
            tbl
        );
        EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', tbl || '_20b_legacy_platform_audit', tbl);
        EXECUTE format(
            'CREATE POLICY %I ON public.%I FOR SELECT TO omega_console, omega_refinement, omega_mcp_infra USING (scope_status = ''legacy_unscoped'' AND omega_20b_platform_audit_context())',
            tbl || '_20b_legacy_platform_audit',
            tbl
        );
    END LOOP;
END $$;

DO $$
BEGIN
    IF to_regclass('public.analytic_apps') IS NOT NULL THEN
        ALTER TABLE public.analytic_apps ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.analytic_apps FORCE ROW LEVEL SECURITY;

        DROP POLICY IF EXISTS analytic_apps_20b_scoped_rw ON public.analytic_apps;
        CREATE POLICY analytic_apps_20b_scoped_rw ON public.analytic_apps
            FOR ALL TO omega_console, omega_refinement
            USING (scope_status = 'scoped' AND omega_rls_workspace_matches(tenant_id, workspace_id))
            WITH CHECK (scope_status = 'scoped' AND omega_rls_workspace_matches(tenant_id, workspace_id));

        DROP POLICY IF EXISTS analytic_apps_20b_template_read ON public.analytic_apps;
        CREATE POLICY analytic_apps_20b_template_read ON public.analytic_apps
            FOR SELECT TO omega_console, omega_refinement
            USING (scope_status = 'platform_template' AND tenant_id IS NULL AND workspace_id IS NULL);

        DROP POLICY IF EXISTS analytic_apps_20b_template_platform_rw ON public.analytic_apps;
        CREATE POLICY analytic_apps_20b_template_platform_rw ON public.analytic_apps
            FOR ALL TO omega_console, omega_refinement
            USING (
                scope_status = 'platform_template'
                AND tenant_id IS NULL
                AND workspace_id IS NULL
                AND omega_20b_platform_audit_context()
            )
            WITH CHECK (
                scope_status = 'platform_template'
                AND tenant_id IS NULL
                AND workspace_id IS NULL
                AND omega_20b_platform_audit_context()
            );

        DROP POLICY IF EXISTS analytic_apps_20b_legacy_platform_audit ON public.analytic_apps;
        CREATE POLICY analytic_apps_20b_legacy_platform_audit ON public.analytic_apps
            FOR SELECT TO omega_console, omega_refinement
            USING (scope_status = 'legacy_unscoped' AND omega_20b_platform_audit_context());
    END IF;
END $$;

-- New public tables must not inherit broad console DML. Future migrations need
-- explicit grants/RLS so the table guard can reason about tenancy.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
   REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM omega_console;

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99w_tenant_isolation_20b.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
