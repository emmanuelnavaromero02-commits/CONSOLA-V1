-- v1.45 stabilization: close remaining scope gaps discovered by production
-- simulation. Tenant pipeline runs must carry tenant_id/workspace_id; only
-- explicitly platform-owned scheduler telemetry may remain global.

DO $$
DECLARE
    platform_writer_roles text;
BEGIN
    SELECT string_agg(quote_ident(rolname), ', ')
      INTO platform_writer_roles
      FROM pg_roles
     WHERE rolname = ANY(ARRAY[
        'omega_mcp_infra',
        'omega_airflow_dag'
     ]);

    IF to_regclass('public.pipeline_runs') IS NOT NULL
       AND platform_writer_roles IS NOT NULL THEN
        DROP POLICY IF EXISTS pipeline_runs_platform_global_rls ON public.pipeline_runs;
        EXECUTE format(
            'CREATE POLICY pipeline_runs_platform_global_rls ON public.pipeline_runs
               FOR ALL
               TO %s
               USING (
                   cartridge_id = ''platform''
                   AND tenant_id IS NULL
                   AND workspace_id IS NULL
               )
               WITH CHECK (
                   cartridge_id = ''platform''
                   AND tenant_id IS NULL
                   AND workspace_id IS NULL
               )',
            platform_writer_roles
        );
    END IF;
END $$;

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99h_scope_hardening.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
