-- Scope dataset definitions by workspace.
--
-- The first beta schema used datasets.name as a global primary key. That made
-- packaged Silver/Gold definitions land in whichever workspace was seeded
-- first, while isolated companies could extract Bronze but never find the
-- matching refinement datasets. The operational contract is workspace scoped:
-- the same packaged dataset name may exist once per workspace.

CREATE TABLE IF NOT EXISTS schema_migrations (
    id BIGSERIAL PRIMARY KEY,
    filename TEXT NOT NULL UNIQUE,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    checksum TEXT
);

ALTER TABLE datasets
    ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id) ON DELETE SET NULL;

ALTER TABLE datasets
    ADD COLUMN IF NOT EXISTS scope_status TEXT NOT NULL DEFAULT 'scoped';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint c
          JOIN pg_class t ON t.oid = c.conrelid
          JOIN pg_namespace n ON n.oid = t.relnamespace
         WHERE n.nspname = 'public'
           AND t.relname = 'datasets'
           AND c.conname = 'datasets_scope_status_check'
    ) THEN
        ALTER TABLE public.datasets
            ADD CONSTRAINT datasets_scope_status_check
            CHECK (scope_status IN ('scoped', 'legacy_unscoped')) NOT VALID;
    END IF;
END $$;

UPDATE datasets d
   SET tenant_id = COALESCE(d.tenant_id, w.tenant_id),
       scope_status = 'scoped'
  FROM workspaces w
 WHERE d.workspace_id = w.id
   AND (d.tenant_id IS NULL OR d.scope_status <> 'scoped');

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conrelid = 'public.datasets'::regclass
           AND conname = 'datasets_pkey'
    ) THEN
        ALTER TABLE public.datasets DROP CONSTRAINT datasets_pkey;
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conrelid = 'public.datasets'::regclass
           AND conname = 'datasets_workspace_name_key'
    ) THEN
        ALTER TABLE public.datasets
            ADD CONSTRAINT datasets_workspace_name_key UNIQUE (workspace_id, name);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_datasets_tenant_workspace
    ON datasets(tenant_id, workspace_id);

CREATE INDEX IF NOT EXISTS idx_datasets_cartridge_workspace
    ON datasets(workspace_id, cartridge, layer);

DO $$
DECLARE
    owner_roles text;
BEGIN
    SELECT string_agg(quote_ident(rolname), ', ')
      INTO owner_roles
      FROM pg_roles
     WHERE rolname = ANY(ARRAY['omega_console', 'omega_refinement']);

    IF owner_roles IS NULL THEN
        RETURN;
    END IF;

    ALTER TABLE public.datasets ENABLE ROW LEVEL SECURITY;
    ALTER TABLE public.datasets FORCE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS console_refinement_scope_rls ON public.datasets;

    EXECUTE format(
        'CREATE POLICY console_refinement_scope_rls ON public.datasets
           FOR ALL TO %s
           USING (scope_status = ''scoped'' AND omega_rls_workspace_matches(tenant_id, workspace_id))
           WITH CHECK (scope_status = ''scoped'' AND omega_rls_workspace_matches(tenant_id, workspace_id))',
        owner_roles
    );
END $$;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omega_console') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON datasets TO omega_console;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omega_refinement') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON datasets TO omega_refinement;
    END IF;
END $$;

INSERT INTO schema_migrations(filename, applied_at)
VALUES ('99zd_datasets_workspace_scoped_catalog.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
