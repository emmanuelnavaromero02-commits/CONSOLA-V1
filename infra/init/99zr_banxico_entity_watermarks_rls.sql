-- Allow the Banxico runtime role to maintain scoped watermarks.
--
-- 99n created the shared entity_watermarks RLS policy before Banxico was
-- added to the scoped cartridge role list. Keep this repair narrow: only the
-- Banxico role gets workspace-scoped access, with no platform-owner bypass.

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omega_cartridge_banxico') THEN
    ALTER TABLE entity_watermarks ENABLE ROW LEVEL SECURITY;
    ALTER TABLE entity_watermarks FORCE ROW LEVEL SECURITY;

    DROP POLICY IF EXISTS entity_watermarks_banxico_workspace_rls ON entity_watermarks;
    CREATE POLICY entity_watermarks_banxico_workspace_rls ON entity_watermarks
      FOR ALL TO omega_cartridge_banxico
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
