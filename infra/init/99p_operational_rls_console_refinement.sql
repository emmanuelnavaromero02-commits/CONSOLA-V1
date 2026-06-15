-- Prompt 11.5: close operational RLS backstop for console/refinement.
--
-- Earlier migrations kept omega_console/omega_refinement as platform-owner
-- readers with unconditional predicates on several operational tables while application
-- scope guards were rolled out. This migration closes only the critical tables
-- covered by live tests: same grants, but rows must match app.tenant_id and
-- app.workspace_id. Auth/provisioning tables remain on the explicit allowlist.

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

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omega_console') THEN
        ALTER ROLE omega_console NOBYPASSRLS;
    END IF;

    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omega_refinement') THEN
        ALTER ROLE omega_refinement NOBYPASSRLS;
    END IF;
END $$;

DO $$
DECLARE
    owner_roles text;
    tbl text;
    tenant_expr text;
    scope_expr text;
    critical_tables CONSTANT text[] := ARRAY[
        'datasets',
        'decisions',
        'control_room_items',
        'control_room_item_events',
        'control_room_action_executions',
        'control_room_thresholds',
        'control_room_lessons',
        'pipeline_runs',
        'copilot_goals',
        'copilot_lessons',
        'rag_sources',
        'rag_chunks',
        'entity_watermarks',
        'token_usage',
        'user_cartridge_overrides',
        'marketplace_orders',
        'tenant_entitlements',
        'cartridge_installations'
    ];
BEGIN
    SELECT string_agg(quote_ident(rolname), ', ')
      INTO owner_roles
      FROM pg_roles
     WHERE rolname = ANY(ARRAY['omega_console', 'omega_refinement']);

    IF owner_roles IS NULL THEN
        RETURN;
    END IF;

    FOREACH tbl IN ARRAY critical_tables
    LOOP
        IF to_regclass('public.' || tbl) IS NULL THEN
            CONTINUE;
        END IF;

        IF NOT EXISTS (
            SELECT 1
              FROM information_schema.columns
             WHERE table_schema = 'public'
               AND table_name = tbl
               AND column_name = 'workspace_id'
        ) THEN
            RAISE EXCEPTION
                'critical operational table %.% has no workspace_id; refusing unscoped owner policy',
                'public',
                tbl;
        END IF;

        IF EXISTS (
            SELECT 1
              FROM information_schema.columns
             WHERE table_schema = 'public'
               AND table_name = tbl
               AND column_name = 'tenant_id'
        ) THEN
            tenant_expr := 'tenant_id';
        ELSE
            tenant_expr := 'NULL::uuid';
        END IF;

        scope_expr := format('omega_rls_workspace_matches(%s, workspace_id)', tenant_expr);

        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', tbl);
        EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY', tbl);

        -- Drop the earlier platform-owner escape hatch for the critical table.
        EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', tbl || '_platform_owner_rls', tbl);
        EXECUTE format('DROP POLICY IF EXISTS console_refinement_scope_rls ON public.%I', tbl);

        EXECUTE format(
            'CREATE POLICY console_refinement_scope_rls ON public.%I
               FOR ALL TO %s
               USING (%s)
               WITH CHECK (%s)',
            tbl,
            owner_roles,
            scope_expr,
            scope_expr
        );
    END LOOP;
END $$;

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99p_operational_rls_console_refinement.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
