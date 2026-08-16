-- 99zzy_analytic_app_grants_owner_rls_repair.sql
--
-- F7-4: let the app-grants ledger functions actually read the state they
-- validate against.
--
-- Root cause: 99zzt runs its reconciliation as SECURITY DEFINER owned by
-- omega_app_grants_owner — deliberately NOLOGIN / NOSUPERUSER / NOBYPASSRLS —
-- and grants that role SELECT on public.cartridge_installations,
-- public.analytic_apps and public.datasets (its ACL block), but never creates
-- RLS policies for it. All three tables are FORCE ROW LEVEL SECURITY, so the
-- grants alone show the role zero rows:
--
--   * cartridge_installations: the "is the cartridge installed and ready"
--     check sees nothing and every activation aborts with
--     'cartridge is not installed for this workspace' (42501). No deployment
--     can ever write an app dataset grant, so every published analytic app's
--     data read fails closed with 403 after the viewer admits the frame.
--   * analytic_apps: the user-created-app guard (created_by_id) resolves to
--     NULL instead of detecting a same-named user app in the caller's scope.
--   * datasets: grant validation cannot see the workspace's dataset rows.
--
-- Forward-only repair (99zzt stays untouched: it is checksum-recorded in
-- schema_migrations): read-only policies for the definer role, scoped with
-- the same omega_rls_workspace_matches expression the tables' existing
-- policies use. The functions already refuse to run without the
-- app.tenant_id / app.workspace_id GUCs, so these policies add no reach
-- beyond the caller's own workspace. Write access stays exactly where 99zzt
-- put it: the two ledger tables the role owns.

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_roles WHERE rolname = 'omega_app_grants_owner'
    ) THEN
        RETURN;
    END IF;

    IF to_regclass('public.cartridge_installations') IS NOT NULL THEN
        DROP POLICY IF EXISTS cartridge_installations_app_grants_owner_read
            ON public.cartridge_installations;
        CREATE POLICY cartridge_installations_app_grants_owner_read
            ON public.cartridge_installations
            FOR SELECT TO omega_app_grants_owner
            USING (public.omega_rls_workspace_matches(tenant_id, workspace_id));
    END IF;

    IF to_regclass('public.analytic_apps') IS NOT NULL THEN
        DROP POLICY IF EXISTS analytic_apps_app_grants_owner_read
            ON public.analytic_apps;
        CREATE POLICY analytic_apps_app_grants_owner_read
            ON public.analytic_apps
            FOR SELECT TO omega_app_grants_owner
            USING (
                scope_status = 'scoped'
                AND public.omega_rls_workspace_matches(tenant_id, workspace_id)
            );
    END IF;

    IF to_regclass('public.datasets') IS NOT NULL THEN
        DROP POLICY IF EXISTS datasets_app_grants_owner_read
            ON public.datasets;
        CREATE POLICY datasets_app_grants_owner_read
            ON public.datasets
            FOR SELECT TO omega_app_grants_owner
            USING (public.omega_rls_workspace_matches(tenant_id, workspace_id));
    END IF;
END $$;

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99zzy_analytic_app_grants_owner_rls_repair.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
