-- Allow the SEC EDGAR runtime role to maintain scoped watermarks.

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omega_cartridge_sec_edgar') THEN
    ALTER TABLE entity_watermarks ENABLE ROW LEVEL SECURITY;
    ALTER TABLE entity_watermarks FORCE ROW LEVEL SECURITY;

    DROP POLICY IF EXISTS entity_watermarks_sec_edgar_workspace_rls ON entity_watermarks;
    CREATE POLICY entity_watermarks_sec_edgar_workspace_rls ON entity_watermarks
      FOR ALL TO omega_cartridge_sec_edgar
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
