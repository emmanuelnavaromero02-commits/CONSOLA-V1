-- 99zzw_sap_cartridge_runlog_scope_repair.sql
--
-- F7-1: repair the 20c runlog scope for the SAP runtime roles.
--
-- Root cause: 99x_cartridge_kb_scope_20c.sql built its role list with the
-- cartridge ids ('omega_cartridge_sap_s4hana', 'omega_cartridge_sap_successfactors'),
-- but 36_cartridge_and_meta_roles.sql creates the runtime roles as
-- omega_cartridge_sap_s4 / omega_cartridge_sap_sf — the names the compose
-- DATABASE_URLs actually use. string_agg over pg_roles silently dropped the
-- two non-existent names, so the 20c policies on run_logs / extraction_runs /
-- jobs / kb_runs never covered the SAP roles and, under FORCE ROW LEVEL
-- SECURITY, every SAP runlog write was denied on fresh installs (the
-- cartridge surfaces this as a 502). Table grants were already in place;
-- only the policies were missing.
--
-- Forward-only repair (99x stays untouched: it is checksum-recorded in
-- schema_migrations): parallel policies with the same 20c expressions for
-- the real role names. pipeline_runs additionally gains the grant + policy
-- that the SuccessFactors-only mirror
-- (cartridges/sap_successfactors/app/services/runlog_service.py::_mirror_pipeline_run)
-- needs — that mirror is best-effort, so its failure only degraded telemetry.

DO $$
DECLARE
    sap_roles text;
    tbl text;
BEGIN
    SELECT string_agg(quote_ident(rolname), ', ')
      INTO sap_roles
      FROM pg_roles
     WHERE rolname = ANY(ARRAY['omega_cartridge_sap_sf', 'omega_cartridge_sap_s4']);

    IF sap_roles IS NULL THEN
        RETURN;
    END IF;

    FOREACH tbl IN ARRAY ARRAY['run_logs', 'extraction_runs', 'jobs', 'kb_runs'] LOOP
        IF to_regclass(format('public.%I', tbl)) IS NULL THEN
            CONTINUE;
        END IF;
        EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', tbl || '_20c_scoped_rls_sap', tbl);
        EXECUTE format(
            'CREATE POLICY %I ON public.%I
               FOR ALL
               TO %s
               USING (scope_status = ''scoped'' AND omega_rls_workspace_matches(tenant_id, workspace_id))
               WITH CHECK (scope_status = ''scoped'' AND omega_rls_workspace_matches(tenant_id, workspace_id))',
            tbl || '_20c_scoped_rls_sap',
            tbl,
            sap_roles
        );
        EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', tbl || '_20c_legacy_platform_audit_rls_sap', tbl);
        EXECUTE format(
            'CREATE POLICY %I ON public.%I
               FOR SELECT
               TO %s
               USING (scope_status = ''legacy_unscoped'' AND omega_20b_platform_audit_context())',
            tbl || '_20c_legacy_platform_audit_rls_sap',
            tbl,
            sap_roles
        );
    END LOOP;
END $$;

DO $$
DECLARE
    sf_role text;
BEGIN
    SELECT quote_ident(rolname)
      INTO sf_role
      FROM pg_roles
     WHERE rolname = 'omega_cartridge_sap_sf';

    IF sf_role IS NULL OR to_regclass('public.pipeline_runs') IS NULL THEN
        RETURN;
    END IF;

    -- Only SuccessFactors mirrors extraction runs into pipeline_runs; the
    -- other cartridge roles deliberately keep no access to this table.
    EXECUTE format('GRANT SELECT, INSERT, UPDATE ON pipeline_runs TO %s', sf_role);
    DROP POLICY IF EXISTS pipeline_runs_tenant_workspace_rls_sap_sf ON pipeline_runs;
    EXECUTE format(
        'CREATE POLICY pipeline_runs_tenant_workspace_rls_sap_sf ON pipeline_runs
           FOR ALL
           TO %s
           USING (omega_rls_workspace_matches(tenant_id, workspace_id))
           WITH CHECK (omega_rls_workspace_matches(tenant_id, workspace_id))',
        sf_role
    );
END $$;

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99zzw_sap_cartridge_runlog_scope_repair.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
