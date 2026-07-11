-- Allow the INEGI runtime role to maintain scoped watermarks.

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omega_cartridge_inegi') THEN
    ALTER TABLE entity_watermarks ENABLE ROW LEVEL SECURITY;
    ALTER TABLE entity_watermarks FORCE ROW LEVEL SECURITY;

    DROP POLICY IF EXISTS entity_watermarks_inegi_workspace_rls ON entity_watermarks;
    CREATE POLICY entity_watermarks_inegi_workspace_rls ON entity_watermarks
      FOR ALL TO omega_cartridge_inegi
      USING (
        current_setting('app.platform_admin', true) = 'true'
        OR omega_rls_workspace_matches(tenant_id, workspace_id)
      )
      WITH CHECK (
        current_setting('app.platform_admin', true) = 'true'
        OR omega_rls_workspace_matches(tenant_id, workspace_id)
      );
  END IF;
END $$;
