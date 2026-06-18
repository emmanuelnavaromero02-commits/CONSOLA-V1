-- Prompt 20C — Cartridge KB scope and residual isolation closure.
--
-- Observability tables store tenant metadata for cartridge jobs/runs. Rows
-- whose scope cannot be inferred stay preserved as legacy_unscoped and are
-- hidden from tenant-scoped sessions.

CREATE OR REPLACE FUNCTION omega_20c_uuid_or_null(value text)
RETURNS uuid
LANGUAGE plpgsql
IMMUTABLE
AS $$
BEGIN
    IF value IS NULL OR value !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' THEN
        RETURN NULL;
    END IF;
    RETURN value::uuid;
END $$;

CREATE OR REPLACE FUNCTION omega_20c_path_scope(value text, marker text)
RETURNS uuid
LANGUAGE sql
IMMUTABLE
AS $$
    SELECT omega_20c_uuid_or_null((regexp_match(coalesce(value, ''), marker || '=([0-9a-fA-F-]{36})'))[1])
$$;

DO $$
DECLARE
    tbl text;
    scoped_roles text;
    audited_tables text[] := ARRAY['jobs', 'run_logs', 'extraction_runs', 'kb_runs'];
BEGIN
    FOREACH tbl IN ARRAY audited_tables LOOP
        IF to_regclass(format('public.%I', tbl)) IS NULL THEN
            CONTINUE;
        END IF;

        EXECUTE format('ALTER TABLE public.%I ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id) ON DELETE SET NULL', tbl);
        EXECUTE format('ALTER TABLE public.%I ADD COLUMN IF NOT EXISTS workspace_id UUID REFERENCES workspaces(id) ON DELETE SET NULL', tbl);
        EXECUTE format('ALTER TABLE public.%I ADD COLUMN IF NOT EXISTS scope_status TEXT NOT NULL DEFAULT ''legacy_unscoped''', tbl);
        EXECUTE format('CREATE INDEX IF NOT EXISTS %I ON public.%I(tenant_id, workspace_id)', 'idx_' || tbl || '_20c_scope', tbl);

        IF NOT EXISTS (
            SELECT 1
              FROM pg_constraint c
              JOIN pg_class rel ON rel.oid = c.conrelid
              JOIN pg_namespace nsp ON nsp.oid = rel.relnamespace
             WHERE nsp.nspname = 'public'
               AND rel.relname = tbl
               AND c.conname = tbl || '_20c_scope_status_check'
        ) THEN
            EXECUTE format(
                'ALTER TABLE public.%I ADD CONSTRAINT %I CHECK (scope_status IN (''scoped'', ''legacy_unscoped'')) NOT VALID',
                tbl,
                tbl || '_20c_scope_status_check'
            );
        END IF;
    END LOOP;

    IF to_regclass('public.jobs') IS NOT NULL THEN
        UPDATE public.jobs
           SET tenant_id = omega_20c_uuid_or_null(args #>> '{security_context,tenant_id}'),
               workspace_id = omega_20c_uuid_or_null(args #>> '{security_context,workspace_id}')
         WHERE (tenant_id IS NULL OR workspace_id IS NULL)
           AND args ? 'security_context';
    END IF;

    IF to_regclass('public.run_logs') IS NOT NULL THEN
        UPDATE public.run_logs
           SET tenant_id = omega_20c_uuid_or_null(detail #>> '{security_context,tenant_id}'),
               workspace_id = omega_20c_uuid_or_null(detail #>> '{security_context,workspace_id}')
         WHERE (tenant_id IS NULL OR workspace_id IS NULL)
           AND detail ? 'security_context';

        UPDATE public.run_logs rl
           SET tenant_id = COALESCE(omega_20c_uuid_or_null(rl.detail #>> '{security_context,tenant_id}'), pr.tenant_id),
               workspace_id = COALESCE(omega_20c_uuid_or_null(rl.detail #>> '{security_context,workspace_id}'), pr.workspace_id)
          FROM public.pipeline_runs pr
         WHERE rl.run_id = pr.run_id
           AND (rl.tenant_id IS NULL OR rl.workspace_id IS NULL);
    END IF;

    IF to_regclass('public.extraction_runs') IS NOT NULL THEN
        UPDATE public.extraction_runs er
           SET tenant_id = COALESCE(pr.tenant_id, omega_20c_path_scope(er.storage_uri, 'tenant_id')),
               workspace_id = COALESCE(pr.workspace_id, omega_20c_path_scope(er.storage_uri, 'workspace_id'))
          FROM public.pipeline_runs pr
         WHERE er.run_id = pr.run_id
           AND (er.tenant_id IS NULL OR er.workspace_id IS NULL);

        UPDATE public.extraction_runs
           SET tenant_id = omega_20c_path_scope(storage_uri, 'tenant_id'),
               workspace_id = omega_20c_path_scope(storage_uri, 'workspace_id')
         WHERE (tenant_id IS NULL OR workspace_id IS NULL)
           AND storage_uri IS NOT NULL;
    END IF;

    IF to_regclass('public.kb_runs') IS NOT NULL THEN
        UPDATE public.kb_runs
           SET tenant_id = omega_20c_path_scope(storage_uri, 'tenant_id'),
               workspace_id = omega_20c_path_scope(storage_uri, 'workspace_id')
         WHERE (tenant_id IS NULL OR workspace_id IS NULL)
           AND storage_uri IS NOT NULL;
    END IF;

    FOREACH tbl IN ARRAY audited_tables LOOP
        IF to_regclass(format('public.%I', tbl)) IS NULL THEN
            CONTINUE;
        END IF;
        EXECUTE format(
            'UPDATE public.%I
                SET scope_status = CASE
                    WHEN tenant_id IS NOT NULL AND workspace_id IS NOT NULL THEN ''scoped''
                    ELSE ''legacy_unscoped''
                END',
            tbl
        );
    END LOOP;

    SELECT string_agg(quote_ident(rolname), ', ')
      INTO scoped_roles
      FROM pg_roles
     WHERE rolname = ANY(ARRAY[
        'omega_console',
        'omega_workspace',
        'omega_mcp_infra',
        'omega_refinement',
        'omega_airflow_dag',
        'omega_cartridge_hubspot',
        'omega_cartridge_replicon',
        'omega_cartridge_sap_hcm',
        'omega_cartridge_sap_s4hana',
        'omega_cartridge_sap_successfactors',
        'omega_cartridge_salesforce'
     ]);

    IF scoped_roles IS NOT NULL THEN
        FOREACH tbl IN ARRAY audited_tables LOOP
            IF to_regclass(format('public.%I', tbl)) IS NULL THEN
                CONTINUE;
            END IF;

            EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', tbl);
            EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY', tbl);
            EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', tbl || '_20c_scoped_rls', tbl);
            EXECUTE format(
                'CREATE POLICY %I ON public.%I
                   FOR ALL
                   TO %s
                   USING (scope_status = ''scoped'' AND omega_rls_workspace_matches(tenant_id, workspace_id))
                   WITH CHECK (scope_status = ''scoped'' AND omega_rls_workspace_matches(tenant_id, workspace_id))',
                tbl || '_20c_scoped_rls',
                tbl,
                scoped_roles
            );
            EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', tbl || '_20c_legacy_platform_audit_rls', tbl);
            EXECUTE format(
                'CREATE POLICY %I ON public.%I
                   FOR SELECT
                   TO %s
                   USING (scope_status = ''legacy_unscoped'' AND omega_20b_platform_audit_context())',
                tbl || '_20c_legacy_platform_audit_rls',
                tbl,
                scoped_roles
            );
        END LOOP;
    END IF;
END $$;

DO $$
DECLARE
    owner_roles text;
BEGIN
    SELECT string_agg(quote_ident(rolname), ', ')
      INTO owner_roles
      FROM pg_roles
     WHERE rolname = ANY(ARRAY['omega_console', 'omega_refinement']);

    IF to_regclass('public.agents') IS NOT NULL AND owner_roles IS NOT NULL THEN
        DROP POLICY IF EXISTS agents_console_refinement_scheduled_read_rls ON public.agents;
        EXECUTE format(
            'CREATE POLICY agents_console_refinement_scheduled_read_rls ON public.agents
               FOR SELECT
               TO %s
               USING (
                   omega_rls_workspace_matches(tenant_id, workspace_id)
                   AND lower(coalesce(extra #>> ''{schedule,enabled}'', ''false'')) IN (''true'', ''1'', ''yes'', ''on'')
               )',
            owner_roles
        );
    END IF;
END $$;

DO $$
BEGIN
    IF to_regclass('public.copilot_lessons') IS NOT NULL THEN
        UPDATE public.copilot_lessons
           SET scope = CASE
               WHEN workspace_id IS NOT NULL THEN 'workspace_global'
               ELSE 'platform_global'
           END
         WHERE scope = 'global';

        ALTER TABLE public.copilot_lessons
            DROP CONSTRAINT IF EXISTS copilot_lessons_scope_check;
        ALTER TABLE public.copilot_lessons
            ADD CONSTRAINT copilot_lessons_scope_check
            CHECK (scope IN ('user', 'workspace', 'global', 'workspace_global', 'tenant_global', 'platform_global')) NOT VALID;
    END IF;
END $$;

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99x_cartridge_kb_scope_20c.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
