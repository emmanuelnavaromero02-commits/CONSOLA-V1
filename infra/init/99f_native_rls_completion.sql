-- v1.45.5: Complete native Postgres RLS coverage for remaining
-- tenant/workspace-scoped operational tables.
--
-- 99d covers intelligence tables and 99e covers most operational surfaces.
-- This migration closes the remaining scoped tables discovered by auditing
-- information_schema after 99e: users, workspaces, user_workspace_roles,
-- pipeline_runs, copilot_goals and copilot_lessons.

CREATE OR REPLACE FUNCTION omega_rls_tenant_matches(row_tenant uuid)
RETURNS boolean
LANGUAGE sql
STABLE
AS $$
    SELECT
        row_tenant IS NOT NULL
        AND NULLIF(current_setting('app.tenant_id', true), '') IS NOT NULL
        AND row_tenant::text = NULLIF(current_setting('app.tenant_id', true), '')
$$;

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
DECLARE
    scoped_roles text;
    owner_roles text;
    tbl text;
    tenant_expr text;
    scope_expr text;
    scoped_tables CONSTANT text[] := ARRAY[
        'pipeline_runs',
        'copilot_goals',
        'copilot_lessons'
    ];
BEGIN
    SELECT string_agg(quote_ident(rolname), ', ')
      INTO scoped_roles
      FROM pg_roles
     WHERE rolname = ANY(ARRAY[
        'omega_workspace',
        'omega_mcp_infra',
        'omega_airflow_dag'
     ]);

    SELECT string_agg(quote_ident(rolname), ', ')
      INTO owner_roles
      FROM pg_roles
     WHERE rolname = ANY(ARRAY[
        'omega_console',
        'omega_refinement'
     ]);

    FOREACH tbl IN ARRAY scoped_tables
    LOOP
        IF to_regclass('public.' || tbl) IS NULL THEN
            CONTINUE;
        END IF;

        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', tbl);
        EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY', tbl);

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

        EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', tbl || '_tenant_workspace_rls', tbl);
        IF scoped_roles IS NOT NULL THEN
            EXECUTE format(
                'CREATE POLICY %I ON public.%I
                   FOR ALL
                   TO %s
                   USING (%s)
                   WITH CHECK (%s)',
                tbl || '_tenant_workspace_rls',
                tbl,
                scoped_roles,
                scope_expr,
                scope_expr
            );
        END IF;

        EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', tbl || '_platform_owner_rls', tbl);
        IF owner_roles IS NOT NULL THEN
            EXECUTE format(
                'CREATE POLICY %I ON public.%I
                   FOR ALL
                   TO %s
                   USING (true)
                   WITH CHECK (true)',
                tbl || '_platform_owner_rls',
                tbl,
                owner_roles
            );
        END IF;
    END LOOP;
END $$;

DO $$
DECLARE
    scoped_roles text;
    owner_roles text;
BEGIN
    SELECT string_agg(quote_ident(rolname), ', ')
      INTO scoped_roles
      FROM pg_roles
     WHERE rolname = ANY(ARRAY['omega_mcp_infra']);

    SELECT string_agg(quote_ident(rolname), ', ')
      INTO owner_roles
      FROM pg_roles
     WHERE rolname = ANY(ARRAY[
        'omega_console',
        'omega_refinement',
        'omega_workspace'
     ]);

    -- Auth bootstrap exception:
    -- workspace resolves the cookie/session into tenant/workspace context by
    -- reading users, workspaces and user_workspace_roles before app.tenant_id
    -- and app.workspace_id can exist on the DB session. Keep native RLS enabled
    -- and forced on these tables, but let the workspace service perform this
    -- bootstrap read. User-facing visibility is still scoped in application
    -- code, and operational tables below remain DB-scoped by tenant/workspace.

    IF to_regclass('public.workspaces') IS NOT NULL THEN
        ALTER TABLE public.workspaces ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.workspaces FORCE ROW LEVEL SECURITY;

        DROP POLICY IF EXISTS workspaces_tenant_rls ON public.workspaces;
        IF scoped_roles IS NOT NULL THEN
            EXECUTE format(
                'CREATE POLICY workspaces_tenant_rls ON public.workspaces
                   FOR ALL
                   TO %s
                   USING (omega_rls_tenant_matches(tenant_id))
                   WITH CHECK (omega_rls_tenant_matches(tenant_id))',
                scoped_roles
            );
        END IF;

        DROP POLICY IF EXISTS workspaces_platform_owner_rls ON public.workspaces;
        IF owner_roles IS NOT NULL THEN
            EXECUTE format(
                'CREATE POLICY workspaces_platform_owner_rls ON public.workspaces
                   FOR ALL
                   TO %s
                   USING (true)
                   WITH CHECK (true)',
                owner_roles
            );
        END IF;
    END IF;

    IF to_regclass('public.users') IS NOT NULL THEN
        ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.users FORCE ROW LEVEL SECURITY;

        DROP POLICY IF EXISTS users_tenant_rls ON public.users;
        IF scoped_roles IS NOT NULL THEN
            EXECUTE format(
                'CREATE POLICY users_tenant_rls ON public.users
                   FOR ALL
                   TO %s
                   USING (omega_rls_tenant_matches(tenant_id))
                   WITH CHECK (omega_rls_tenant_matches(tenant_id))',
                scoped_roles
            );
        END IF;

        DROP POLICY IF EXISTS users_platform_owner_rls ON public.users;
        IF owner_roles IS NOT NULL THEN
            EXECUTE format(
                'CREATE POLICY users_platform_owner_rls ON public.users
                   FOR ALL
                   TO %s
                   USING (true)
                   WITH CHECK (true)',
                owner_roles
            );
        END IF;
    END IF;

    IF to_regclass('public.user_workspace_roles') IS NOT NULL THEN
        ALTER TABLE public.user_workspace_roles ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.user_workspace_roles FORCE ROW LEVEL SECURITY;

        DROP POLICY IF EXISTS user_workspace_roles_workspace_rls ON public.user_workspace_roles;
        IF scoped_roles IS NOT NULL THEN
            EXECUTE format(
                'CREATE POLICY user_workspace_roles_workspace_rls ON public.user_workspace_roles
                   FOR ALL
                   TO %s
                   USING (
                       EXISTS (
                           SELECT 1 FROM public.workspaces w
                            WHERE w.id = user_workspace_roles.workspace_id
                              AND omega_rls_workspace_matches(w.tenant_id, w.id)
                       )
                   )
                   WITH CHECK (
                       EXISTS (
                           SELECT 1 FROM public.workspaces w
                            WHERE w.id = user_workspace_roles.workspace_id
                              AND omega_rls_workspace_matches(w.tenant_id, w.id)
                       )
                   )',
                scoped_roles
            );
        END IF;

        DROP POLICY IF EXISTS user_workspace_roles_platform_owner_rls ON public.user_workspace_roles;
        IF owner_roles IS NOT NULL THEN
            EXECUTE format(
                'CREATE POLICY user_workspace_roles_platform_owner_rls ON public.user_workspace_roles
                   FOR ALL
                   TO %s
                   USING (true)
                   WITH CHECK (true)',
                owner_roles
            );
        END IF;
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omega_mcp_infra') THEN
        ALTER ROLE omega_mcp_infra NOBYPASSRLS;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omega_workspace') THEN
        ALTER ROLE omega_workspace NOBYPASSRLS;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omega_airflow_dag') THEN
        ALTER ROLE omega_airflow_dag NOBYPASSRLS;
    END IF;
END $$;

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99f_native_rls_completion.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
